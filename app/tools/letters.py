# -*- coding: utf-8 -*-
"""
Локальные письма и трекер откликов: draft_letter, track_response.

Жёсткое ограничение продукта: агент НИЧЕГО не отправляет. Письма сохраняются
только в data/outbox/, отклики учитываются в локальном data/tracker.db.
API откликов hh (/negotiations) не используется и недоступен (403 user_auth_expected).
"""
import os
import re
import sqlite3
import time

from google.adk.tools import ToolContext

from .. import config

VALID_STATUSES = ["черновик", "отправлено", "просмотрено", "приглашение", "отказ"]


def _uid(tool_context: ToolContext = None):
    """Идентификатор пользователя для изоляции данных (изолировано по user_id, не по данным клиента).
    Без контекста (прямые вызовы/тесты) — 'local'."""
    if tool_context is None:
        return "local"
    u = getattr(tool_context, "user_id", None)
    return str(u) if u else "local"

OUTBOX_NOTE = ("Письмо сохранено локально и НЕ отправлено. Скопируйте текст "
               "и отправьте работодателю самостоятельно (кнопка «копировать» в интерфейсе).")
TRACKER_NOTE = ("Статус отклика меняете только вы вручную: агент не отправляет отклики "
                "и не читает переписку (API откликов недоступен).")


# ------------------------------------------------------------ файлы писем
# Формат файла: строка «# <название>», маркеры «- ключ: значение», «---», ТЕКСТ письма
# (копируется пользователем — только он, без шапки).
META_KEYS = ["вакансия", "работодатель", "ссылка", "тон", "создано", "статус"]


def make_letter_title(vacancy_name, employer):
    """Название черновика по вакансии и работодателю (пользователь может переименовать)."""
    parts = [p for p in (str(employer or "").strip(), str(vacancy_name or "").strip()) if p]
    title = ("Сопроводительное — " + ": ".join(parts[:2])) if parts else "Сопроводительное письмо"
    return title[:97] + "…" if len(title) > 100 else title


def parse_letter(text):
    """Разбор файла письма -> {title, meta, body}. Понимает и старый формат."""
    lines = (text or "").splitlines()
    title, meta, body = "", {}, (text or "").strip()
    sep = next((i for i, l in enumerate(lines) if l.strip() == "---"), None)
    first = next((i for i, l in enumerate(lines) if l.strip()), None)
    if first is not None and lines[first].lstrip().startswith("# ") and sep is not None:
        title = lines[first].lstrip()[2:].strip()
        for l in lines[first + 1:sep]:
            if l.startswith("- ") and ":" in l:
                k, v = l[2:].split(":", 1)
                meta[k.strip()] = v.strip()
        body = "\n".join(lines[sep + 1:]).strip("\n")
    return {"title": title, "meta": meta, "body": body}


def render_letter(title, meta, body):
    """Собрать файл письма обратно (мета — по известным ключам в фиксированном порядке)."""
    head = ["# " + (title or "Письмо работодателю"), ""]
    keys = META_KEYS + [k for k in meta if k not in META_KEYS]
    for k in keys:
        if k in meta and meta[k] not in (None, ""):
            head.append(f"- {k}: {meta[k]}")
    return "\n".join(head) + "\n\n---\n\n" + (body or "").strip() + "\n"


def draft_letter(vacancy_id: str, letter_text: str, tone: str = "деловой",
                 tool_context: ToolContext = None):
    """Сохранить черновик письма работодателю ЛОКАЛЬНО (data/outbox/). Ничего не отправляется.

    Args:
        vacancy_id: id вакансии (из search_vacancies / match_profile).
        letter_text: полный текст письма/сопроводительного (пишет агент, копирует пользователь).
            Пиши естественно, как живой человек: короткие простые предложения, конкретика
            из резюме и вакансии, без канцелярита и ИИ-штампов («хотелось бы выразить интерес»,
            «в заключение», эмодзи, восклицательные знаки).
        tone: тон письма для пометки в файле ("деловой", "дружелюбный", "краткий").

    Returns:
        dict: путь к сохранённому файлу, карточка вакансии и напоминание, что отправки нет.
    """
    text = (letter_text or "").strip()
    if len(text) < 50:
        return {"error": "текст письма слишком короткий (минимум 50 символов)"}
    if len(text) > 5000:
        return {"error": "текст письма слишком длинный (максимум 5000 символов)"}
    con = sqlite3.connect(config.APP_DB, timeout=30)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT name, employer, url FROM vacancies WHERE vacancy_id=?",
                    (str(vacancy_id),)).fetchone()
    con.close()
    if not r:
        return {"error": "вакансия не найдена: " + str(vacancy_id)}

    os.makedirs(config.OUTBOX_DIR, exist_ok=True)
    user_dir = os.path.join(config.OUTBOX_DIR, _uid(tool_context))
    os.makedirs(user_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    title = make_letter_title(r["name"], r["employer"])
    fname = f"{vacancy_id}_{ts}.md"
    path = os.path.join(user_dir, fname)
    text_out = render_letter(title, {
        "вакансия": r["name"], "работодатель": r["employer"], "ссылка": r["url"],
        "тон": tone, "создано": ts, "статус": "черновик, не отправлено"}, text)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text_out)
    return {
        "saved_to": path, "file": fname, "title": title,
        "vacancy": {"vacancy_id": str(vacancy_id), "name": r["name"],
                    "employer": r["employer"], "url": r["url"]},
        "note": OUTBOX_NOTE,
    }


def parse_follow_up(text):
    """Дата напоминания из фразы: '2026-11-07' | 'через 7 дней' | 'через неделю' | 'через месяц'.

    Возвращает 'YYYY-MM-DD' или '' (нет напоминания)."""
    import datetime as _dt
    t = (text or "").strip().lower()
    if not t:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", t)
    if m:
        return t
    words = {"неделю": 7, "недели": 7, "неделя": 7, "месяц": 30, "месяца": 30,
             "два месяца": 60, "две недели": 14, "дня": 2, "день": 1, "дней": 7}
    m = re.search(r"через\s+(\d+)\s+(дн\w*|недел\w*|месяц\w*)", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        mult = 1 if unit.startswith("дн") else (7 if unit.startswith("недел") else 30)
        return (_dt.date.today() + _dt.timedelta(days=n * mult)).isoformat()
    m = re.search(r"через\s+(\S+)", t)
    if m and m.group(1) in words:
        return (_dt.date.today() + _dt.timedelta(days=words[m.group(1)])).isoformat()
    return ""


_TRACKER_SCHEMA = """CREATE TABLE IF NOT EXISTS responses (
    vacancy_id TEXT NOT NULL,
    user_id    TEXT NOT NULL DEFAULT 'local',
    name       TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, vacancy_id))"""


def _tracker_con():
    con = sqlite3.connect(config.TRACKER_DB, timeout=30)
    con.row_factory = sqlite3.Row
    cols = [r[1] for r in con.execute("PRAGMA table_info(responses)")]
    if not cols:
        con.execute(_TRACKER_SCHEMA)
    elif "user_id" not in cols:  # миграция старой однопользовательской схемы
        con.execute("ALTER TABLE responses RENAME TO responses_old")
        con.execute(_TRACKER_SCHEMA)
        con.execute("""INSERT INTO responses (vacancy_id, user_id, name, status, note, updated_at)
                       SELECT vacancy_id, 'local', name, status, note, updated_at
                       FROM responses_old""")
        con.execute("DROP TABLE responses_old")
        con.commit()
    if "follow_up_at" not in [r[1] for r in con.execute("PRAGMA table_info(responses)")]:
        con.execute("ALTER TABLE responses ADD COLUMN follow_up_at TEXT NOT NULL DEFAULT ''")
        con.commit()
    return con


def track_response(vacancy_id: str = "", status: str = "", note: str = "",
                   follow_up: str = "", tool_context: ToolContext = None):
    """Учёт откликов ЛОКАЛЬНО: поставить/обновить статус, заметку и напоминание или показать список.

    Args:
        vacancy_id: id вакансии. Если пусто — вернуть список отслеживаемых откликов.
        status: "черновик" | "отправлено" | "просмотрено" | "приглашение" | "отказ"
        (статус ставит пользователь вручную — агент ничего не отправляет).
        note: заметка пользователя (например "написал рекрутеру в телеграме").
        follow_up: напоминание «связаться позже»: дата "2026-11-07" или фраза
        "через неделю" / "через 10 дней" / "через месяц" (пусто — не менять/без напоминания).

    Returns:
        dict: обновлённая запись или список откликов (топ-20 по дате обновления).
    """
    uid = _uid(tool_context)
    con = _tracker_con()
    if not (vacancy_id or "").strip():
        rows = con.execute("SELECT * FROM responses WHERE user_id=? "
                           "ORDER BY updated_at DESC LIMIT 20", (uid,)).fetchall()
        con.close()
        return {"responses": [dict(r) for r in rows], "count": len(rows), "note": TRACKER_NOTE}
    vid = str(vacancy_id).strip()
    st = (status or "").strip().lower()
    if st not in VALID_STATUSES:
        con.close()
        return {"error": "недопустимый статус: " + str(status), "valid": VALID_STATUSES}
    fu = parse_follow_up(follow_up) if follow_up else ""
    name = ""
    c2 = sqlite3.connect(config.APP_DB, timeout=30)
    c2.execute("PRAGMA query_only=ON")
    row = c2.execute("SELECT name FROM vacancies WHERE vacancy_id=?", (vid,)).fetchone()
    c2.close()
    if row:
        name = row[0]
    con.execute("""INSERT INTO responses (vacancy_id, user_id, name, status, note, updated_at, follow_up_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(user_id, vacancy_id) DO UPDATE SET status=excluded.status,
                       note=excluded.note, updated_at=excluded.updated_at,
                       follow_up_at=CASE WHEN excluded.follow_up_at != ''
                                         THEN excluded.follow_up_at
                                         ELSE responses.follow_up_at END""",
                (vid, uid, name, st, (note or "").strip(),
                 time.strftime("%Y-%m-%dT%H:%M:%S"), fu))
    con.commit()
    rec = dict(con.execute("SELECT * FROM responses WHERE user_id=? AND vacancy_id=?",
                           (uid, vid)).fetchone())
    con.close()
    return {"updated": rec, "note": TRACKER_NOTE}
