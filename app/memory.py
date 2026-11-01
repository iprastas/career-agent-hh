# -*- coding: utf-8 -*-
"""
Контекстная память диалога: автоматическое сжатие длинной истории в сводку.

Как это работает:
- ADK DatabaseSessionService хранит ВСЕ события сессии, и каждый ход модель получает
  всю историю целиком — при длинном диалоге контекст переполняется (и дорожает);
- перед ходом история оценивается по бюджету (символы + события); если он превышен,
  старая часть диалога сжимается LLM в короткую сводку, старые события удаляются
  из БД, сводка подставляется вместо них, последние события остаются как есть;
- сжатие видно в интерфейсе (событие status «память: …»), итоговая сводка — тоже
  часть истории диалога; при недоступности модели работает детерминированный фолбэк.
"""
import os
import sqlite3

from . import config

# Бюджет контекста (символьный) и размер окна
CHAR_BUDGET = 12_000   # суммарный текст истории, после которого включается сжатие
MIN_MESSAGES = 8       # короче не трогаем
KEEP_EVENTS = 10       # сколько последних событий оставляем без изменений

SUMMARY_PREFIX = ("[Сводка предыдущей части диалога — история автоматически сжата, "
                  "оригинал удалён из контекста]\n")

_SUMMARIZE_PROMPT = """Ты сжимаешь протокол диалога карьерного агента с соискателем.
Кратко (8-15 предложений, по-русски) воспроизведи суть: о чём спрашивали, что ответил
агент. Обязательно сохрани конкретику: города, навыки, названия и id вакансий, цифры
зарплат, договорённости и то, что уже сделано (письма, отклики). Без вступлений и выводов."""


def extract_messages(session):
    """Текстовые сообщения сессии: [{'role': 'user'|'agent', 'text': ...}]."""
    msgs = []
    for ev in getattr(session, "events", []) or []:
        if not (ev.content and ev.content.parts):
            continue
        role = "user" if getattr(ev.content, "role", "") == "user" else "agent"
        text = " ".join(p.text for p in ev.content.parts if getattr(p, "text", None))
        if text.strip():
            msgs.append({"role": role, "text": text.strip()})
    return msgs


def dialog_title(text, max_len=60):
    """Заголовок диалога: первая фраза первого сообщения, обрезанная по словам."""
    import re
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return "Диалог"
    first = re.split(r"(?<=[.!?…])\s", t)[0]
    if len(first) > max_len:
        cut = first[:max_len]
        sp = cut.rfind(" ")
        first = cut[:sp if sp > 10 else max_len].rstrip(",;:—-") + "…"
    return first[0].upper() + first[1:] if len(first) > 1 else first


def _fallback_summary(msgs):
    """Детерминированный фолбэк, если LLM недоступна: темы из реплик пользователя."""
    topics = [m["text"][:160] for m in msgs if m["role"] == "user"][:6]
    if not topics:
        topics = [m["text"][:160] for m in msgs[:4]]
    return "Обсуждали ранее: " + " | ".join(topics)


async def _llm_summarize(text):
    import litellm
    resp = await litellm.acompletion(
        model=config.model_name(),
        messages=[{"role": "system", "content": _SUMMARIZE_PROMPT},
                  {"role": "user", "content": text[:20_000]}],
        temperature=0.2, max_tokens=500, timeout=60)
    return (resp.choices[0].message.content or "").strip()


def _delete_events(user_id, session_id, event_ids):
    """Точечное удаление старых событий из sessions.db (у ADK нет API на это)."""
    if not event_ids:
        return
    db = os.path.join(config.DATA, "sessions.db")
    con = sqlite3.connect(db, timeout=30)
    try:
        for i in range(0, len(event_ids), 50):
            chunk = event_ids[i:i + 50]
            marks = ",".join("?" * len(chunk))
            con.execute(f"DELETE FROM events WHERE app_name IS NOT NULL AND user_id=? "
                        f"AND session_id=? AND id IN ({marks})",
                        (user_id, session_id, *chunk))
        con.commit()
    finally:
        con.close()


async def compact_if_needed(runner, user_id, session_id):
    """Сжать историю сессии, если она вылезла за бюджет. Возвращает текст сводки или None.

    Никогда не бросает исключений: сжатие — фоновая оптимизация, а не условие хода.
    """
    try:
        svc = runner.session_service
        session = await svc.get_session(app_name=runner.app_name, user_id=user_id,
                                        session_id=session_id)
        if session is None:
            return None
        events = list(session.events)
        msgs = extract_messages(session)
        total = sum(len(m["text"]) for m in msgs)
        if len(msgs) < MIN_MESSAGES or total < CHAR_BUDGET or len(events) <= KEEP_EVENTS:
            return None

        old_events, kept = events[:-KEEP_EVENTS], events[-KEEP_EVENTS:]
        old_msgs = []
        for ev in old_events:
            if not (ev.content and ev.content.parts):
                continue
            role = "user" if getattr(ev.content, "role", "") == "user" else "agent"
            text = " ".join(p.text for p in ev.content.parts if getattr(p, "text", None))
            if text.strip():
                old_msgs.append({"role": role, "text": text.strip()})
        if not old_msgs:
            return None

        history_text = "\n".join(f"{m['role']}: {m['text']}" for m in old_msgs)
        try:
            summary = await _llm_summarize(history_text)
            if not summary:
                summary = _fallback_summary(old_msgs)
        except Exception:
            summary = _fallback_summary(old_msgs)

        _delete_events(user_id, session_id, [e.id for e in old_events if getattr(e, "id", "")])

        from google.adk.events.event import Event
        from google.genai import types
        ev = Event(author="user", invocation_id="context_compaction",
                   content=types.Content(role="user",
                                         parts=[types.Part(text=SUMMARY_PREFIX + summary)]))
        await svc.append_event(session, ev)
        return summary
    except Exception:
        return None
