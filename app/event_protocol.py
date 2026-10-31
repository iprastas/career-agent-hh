# -*- coding: utf-8 -*-
"""
Протокол событий агента — единый «провод» для консольного CLI и SSE (FastAPI).

Типы конверта (стабильны; UI строится по ним):
  refusal    — отказ без вызова модели (слой 1 guardrails) или замена ответа (слой 3)
  status     — наррация модели перед вызовом инструмента («сейчас поищу…»)
  tool_call  — имя инструмента + аргументы
  tool_result— результат (обрезанный)
  think      — reasoning-части модели (визуализация «агент думает»)
  delta      — потоковые фрагменты текста ответа
  answer     — итоговый ответ (после пост-фильтра)
  done       — мета: инструменты, время, refused/ошибка

Классификация частей (thinking vs answer) делается на этапе разбора событий —
это data-shape правило: оно же кормит SSE-конверт и пост-фильтр.
"""
import time

from . import config, guardrails

_TOOL_RESULT_MAX = 600  # обрезка результата для контекста/UI


def _part_text(p):
    return getattr(p, "text", None)


async def stream_turn(runner, user_id, session_id, text):
    """Один ход пользователя -> async-генератор конвертов событий."""
    from google.genai import types

    ok, reason = guardrails.pre_filter(text)
    if not ok:
        yield {"type": "refusal", "reason": reason, "text": guardrails.refusal_for(reason)}
        yield {"type": "done", "tools": [], "elapsed": 0.0, "refused": True}
        return

    t0 = time.time()
    tools_used = []
    buf = []          # сегмент текста между границами (вызовами инструментов)
    saw_partial = False
    answer_text = ""

    msg = types.Content(role="user", parts=[types.Part(text=text)])
    async for ev in runner.run_async(user_id=user_id, session_id=session_id, new_message=msg):
        if not (ev.content and ev.content.parts):
            continue
        for p in ev.content.parts:
            fc = getattr(p, "function_call", None)
            fr = getattr(p, "function_response", None)
            if fc is not None:
                if buf:
                    yield {"type": "status", "text": "".join(buf).strip()}
                    buf, saw_partial = [], False
                name = getattr(fc, "name", "?")
                args = dict(getattr(fc, "args", {}) or {})
                tools_used.append(name)
                yield {"type": "tool_call", "name": name, "args": args}
                continue
            if fr is not None:
                raw = getattr(fr, "response", None)
                s = raw if isinstance(raw, str) else str(raw)
                yield {"type": "tool_result", "name": getattr(fr, "name", "?"),
                       "text": s[:_TOOL_RESULT_MAX],
                       # полный результат — для карточек UI (контекст модели это не кормит)
                       "payload": raw if isinstance(raw, (dict, list)) else None}
                continue
            t = _part_text(p)
            if not t:
                continue
            if getattr(p, "thought", False):
                yield {"type": "think", "text": t}
                continue
            if getattr(ev, "partial", False):
                saw_partial = True
                buf.append(t)
                yield {"type": "delta", "text": t}
            else:
                if not saw_partial:
                    buf.append(t)
                # иначе: это финальная копия уже полученных дельт — не дублируем

    answer_text = "".join(buf).strip()
    if answer_text:
        from .system_prompt import BASE_PROMPT
        safe = guardrails.post_filter(answer_text, BASE_PROMPT)
        if safe != answer_text:
            yield {"type": "refusal", "reason": "post_filter", "text": safe}
            answer_text = ""
        else:
            yield {"type": "answer", "text": safe}
    yield {"type": "done", "tools": tools_used,
           "elapsed": round(time.time() - t0, 2), "refused": False}
