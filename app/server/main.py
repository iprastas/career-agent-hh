# -*- coding: utf-8 -*-
"""
FastAPI-бэкенд карьерного агента.

Архитектурные решения:
- асинхронный FastAPI, один ADK Runner на процесс + DatabaseSessionService (data/sessions.db):
  контекст диалогов переживает перезапуск, сессии изолированы по user_id из токена;
- авторизация: email+пароль (PBKDF2), токен в заголовке Authorization: Bearer (только серверные
  сессии; клиентский user_id не принимается нигде — изоляция это правило выборки, не промпта);
- SSE: POST /api/chat/stream отдаёт text/event-stream с конвертами event_protocol (think/tool_call/
  delta/answer/done) — UI видит ход рассуждений агента в реальном времени;
- инструменты агента ничего не отправляют наружу: REST-эндпоинты только читают датасет и
  управляют локальными записями (outbox, tracker) в рамках пользователя.

Запуск: .venv/Scripts/python -m uvicorn app.server.main:app --port 8000
"""
import json
import os
import types

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import config

config.apply_env()  # ключи провайдера — до импорта litellm/ADK (см. app/agent.py)

from .. import guardrails  # noqa: E402
from ..agent import build_agent, APP_NAME  # noqa: E402
from ..event_protocol import stream_turn  # noqa: E402
from ..memory import compact_if_needed, dialog_title, extract_messages  # noqa: E402
from ..tools import (draft_letter, fresh_vacancies, match_profile,  # noqa: E402
                     salary_stats, salary_trend, search_vacancies, semantic_search,
                     similar_vacancies, skill_gap, track_response, vacancy_details)
from ..tools.letters import make_letter_title, parse_letter, render_letter  # noqa: E402
from . import store  # noqa: E402

app = FastAPI(title="Карьерный агент hh.ru", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

_RUNNER = None


def _runner():
    """Один Runner на процесс; история диалогов — в SQLite (переживает перезапуск)."""
    global _RUNNER
    if _RUNNER is None:
        from google.adk.runners import Runner
        from google.adk.sessions import DatabaseSessionService
        db_path = os.path.join(config.DATA, "sessions.db").replace(os.sep, "/")
        svc = DatabaseSessionService(db_url="sqlite+aiosqlite:///" + db_path)
        _RUNNER = Runner(agent=build_agent(), app_name=APP_NAME, session_service=svc)
    return _RUNNER


# ---------------------------------------------------------------- авторизация
class AuthIn(BaseModel):
    email: str
    password: str


class ChatIn(BaseModel):
    message: str
    session_id: str = ""


class TrackerIn(BaseModel):
    vacancy_id: str
    status: str
    note: str = ""
    follow_up: str = ""


class ProfileIn(BaseModel):
    profile_id: str = "anastasia"
    title: str = ""
    location: str = ""
    experience_years: int = 0
    salary_net: int = 0


class SessionPatch(BaseModel):
    title: str


class LetterPatch(BaseModel):
    title: str | None = None
    body: str | None = None


def current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "требуется вход: заголовок Authorization: Bearer <token>")
    u = store.user_by_token(authorization[7:].strip())
    if not u:
        raise HTTPException(401, "сессия истекла или токен недействителен")
    return u


def _uid(user):
    return "u" + str(user["id"])


def _ctx(user):
    """Дак-тип вместо ToolContext для REST-вызовов инструментов (та же изоляция по user_id)."""
    return types.SimpleNamespace(user_id=_uid(user))


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/register")
def register(body: AuthIn):
    res = store.register(body.email, body.password)
    if "error" in res:
        raise HTTPException(400, res["error"])
    return res


@app.post("/api/login")
def login(body: AuthIn):
    res = store.login(body.email, body.password)
    if "error" in res:
        raise HTTPException(401, res["error"])
    return res


@app.post("/api/logout")
def logout(authorization: str = Header(None)):
    store.logout((authorization or "").removeprefix("Bearer ").strip())
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"user": user}


# ---------------------------------------------------------------- сессии диалога
@app.post("/api/sessions")
async def create_session(user=Depends(current_user)):
    s = await _runner().session_service.create_session(app_name=APP_NAME, user_id=_uid(user))
    return {"session_id": s.id}


@app.get("/api/sessions")
async def list_sessions(user=Depends(current_user)):
    """Список диалогов с названиями. Пустые диалоги (без сообщений) не сохраняются:
    они удаляются и при выдаче (гигиена старых), и в chat_stream после хода."""
    runner = _runner()
    uid = _uid(user)
    resp = await runner.session_service.list_sessions(app_name=APP_NAME, user_id=uid)
    sessions = getattr(resp, "sessions", resp) or []
    titles = store.get_session_titles(uid)
    import time as _time
    out = []
    for s in sorted(sessions, key=lambda x: getattr(x, "last_update_time", 0) or 0, reverse=True):
        full = await runner.session_service.get_session(app_name=APP_NAME, user_id=uid,
                                                        session_id=s.id)
        msgs = extract_messages(full) if full else []
        if not msgs:
            # пустой диалог не сохраняем; но сессия «в процессе хода» ещё пустая —
            # чистим только то, что пустует дольше 5 минут (не мешаем идущему ходу)
            created = getattr(s, "create_time", None)
            age = (_time.time() - created.timestamp()) if created else 0
            if age > 300:
                await runner.session_service.delete_session(app_name=APP_NAME, user_id=uid,
                                                            session_id=s.id)
                store.delete_session_meta(uid, s.id)
            continue
        if s.id not in titles:  # легаси-диалоги без названия — из первого сообщения
            titles[s.id] = store.set_session_title(uid, s.id, dialog_title(msgs[0]["text"]))
        out.append({"session_id": s.id, "title": titles[s.id],
                    "messages": len(msgs),
                    "updated_at": getattr(s, "last_update_time", None)})
        if len(out) >= 30:
            break
    return {"sessions": out}


@app.get("/api/sessions/{session_id}/history")
async def history(session_id: str, user=Depends(current_user)):
    s = await _runner().session_service.get_session(app_name=APP_NAME, user_id=_uid(user),
                                                    session_id=session_id)
    if s is None:
        raise HTTPException(404, "сессия не найдена")
    msgs = extract_messages(s)
    if not msgs:  # пустой диалог — не существует как диалог
        raise HTTPException(404, "диалог пуст")
    titles = store.get_session_titles(_uid(user))
    return {"session_id": session_id, "title": titles.get(session_id, ""),
            "messages": msgs}


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, body: SessionPatch, user=Depends(current_user)):
    """Переименование диалога пользователем (название в списке слева)."""
    s = await _runner().session_service.get_session(app_name=APP_NAME, user_id=_uid(user),
                                                    session_id=session_id)
    if s is None:
        raise HTTPException(404, "сессия не найдена")
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(400, "название не может быть пустым")
    title = store.set_session_title(_uid(user), session_id, title)
    return {"session_id": session_id, "title": title}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, user=Depends(current_user)):
    """Удаление диалога вместе с историей (только свои диалоги — по user_id из токена)."""
    runner = _runner()
    uid = _uid(user)
    s = await runner.session_service.get_session(app_name=APP_NAME, user_id=uid,
                                                 session_id=session_id)
    if s is None:
        raise HTTPException(404, "сессия не найдена")
    await runner.session_service.delete_session(app_name=APP_NAME, user_id=uid,
                                                session_id=session_id)
    store.delete_session_meta(uid, session_id)
    return {"ok": True}


# ---------------------------------------------------------------- SSE-чат
@app.post("/api/chat/stream")
async def chat_stream(body: ChatIn, user=Depends(current_user)):
    runner = _runner()
    uid = _uid(user)
    sid = body.session_id or ""
    if sid:
        s = await runner.session_service.get_session(app_name=APP_NAME, user_id=uid,
                                                     session_id=sid)
        if s is None:
            raise HTTPException(404, "сессия не найдена")

    def sse(env):
        return "event: " + env["type"] + "\ndata: " + json.dumps(env, ensure_ascii=False) + "\n\n"

    async def gen():
        nonlocal sid
        # Пустые диалоги не сохраняются: сессия создаётся, только если сообщение прошло
        # пред-фильтр guardrails (отказ/инъекция — без записи в историю диалогов).
        ok, reason = guardrails.pre_filter(body.message)
        if not ok:
            yield sse({"type": "session", "session_id": sid})
            yield sse({"type": "refusal", "reason": reason,
                       "text": guardrails.refusal_for(reason)})
            yield sse({"type": "done", "tools": [], "elapsed": 0.0, "refused": True})
            return
        if not sid:
            s = await runner.session_service.create_session(app_name=APP_NAME, user_id=uid)
            sid = s.id
            store.set_session_title(uid, sid, dialog_title(body.message))
        yield sse({"type": "session", "session_id": sid})
        try:
            # память: длинная история сжимается в сводку перед ходом (см. app/memory.py)
            summary = await compact_if_needed(runner, uid, sid)
            if summary:
                yield sse({"type": "status",
                           "text": "память: длинная история диалога сжата в короткую сводку"})
            async for env in stream_turn(runner, uid, sid, body.message):
                yield sse(env)
        except Exception as e:  # модель/сеть — не роняем поток молча
            yield sse({"type": "error", "text": "ошибка агента: " + str(e)[:200]})
            yield sse({"type": "done", "tools": [], "elapsed": 0.0, "refused": False})
            # ход не состоялся и сообщений не появилось — диалог не сохраняем
            s2 = await runner.session_service.get_session(app_name=APP_NAME, user_id=uid,
                                                          session_id=sid)
            if s2 is None or not extract_messages(s2):
                if s2 is not None:
                    await runner.session_service.delete_session(app_name=APP_NAME, user_id=uid,
                                                                session_id=sid)
                    store.delete_session_meta(uid, sid)
                sid = ""
                yield sse({"type": "session", "session_id": ""})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


# ---------------------------------------------------------------- REST-данные
@app.get("/api/vacancies")
def vacancies(q: str, city: str = "", experience: str = "", salary_min: int = 0,
              schedule: str = "", limit: int = 5, user=Depends(current_user)):
    return search_vacancies(q, city=city, experience=experience, salary_min=salary_min,
                            schedule=schedule, limit=limit)


@app.get("/api/vacancies/{vacancy_id}")
def vacancy_card(vacancy_id: str, user=Depends(current_user)):
    """Полная карточка вакансии (для раскрытия карточки в интерфейсе)."""
    res = vacancy_details(vacancy_id)
    if "error" in res:
        raise HTTPException(404, res["error"])
    return res


@app.get("/api/match")
def match(profile: str = "anastasia", top: int = 5, user=Depends(current_user)):
    return match_profile(profile, top_n=top)


@app.get("/api/gap")
def gap(profile: str = "anastasia", top: int = 10, user=Depends(current_user)):
    return skill_gap(profile, top_n=top)


@app.get("/api/stats/salary")
def stats(skill: str = "", city: str = "", experience: str = "", period_days: int = 0,
          user=Depends(current_user)):
    return salary_stats(skill=skill, city=city, experience=experience, period_days=period_days)


@app.get("/api/stats/salary_trend")
def stats_trend(skill: str = "", city: str = "", months: int = 24, user=Depends(current_user)):
    return salary_trend(skill=skill, city=city, months=months)


@app.get("/api/semantic")
def semantic(q: str, city: str = "", limit: int = 8, user=Depends(current_user)):
    return semantic_search(q, city=city, limit=limit)


@app.get("/api/similar/{vacancy_id}")
def similar(vacancy_id: str, limit: int = 6, user=Depends(current_user)):
    return similar_vacancies(vacancy_id, limit=limit)


@app.get("/api/fresh")
def fresh(profile: str = "anastasia", days: int = 7, limit: int = 8, user=Depends(current_user)):
    return fresh_vacancies(profile_id=profile, days=days, limit=limit)


# ---------------------------------------------------------------- профиль соискателя
def _profile_path(profile_id):
    pid = os.path.basename((profile_id or "anastasia").strip()) or "anastasia"
    if not pid.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "некорректный id профиля")
    return os.path.join(config.PROFILES_DIR, pid + ".json"), pid


@app.get("/api/profile")
def profile_get(profile_id: str = "anastasia", user=Depends(current_user)):
    path, pid = _profile_path(profile_id)
    if not os.path.exists(path):
        raise HTTPException(404, "профиль не найден: " + pid)
    data = json.load(open(path, encoding="utf-8"))
    files = sorted(f[:-5] for f in os.listdir(config.PROFILES_DIR)
                   if f.endswith(".json") and f != "synthetic.json")
    return {"profile_id": pid, "profiles": files, "data": data}


@app.put("/api/profile")
def profile_put(body: ProfileIn, user=Depends(current_user)):
    """Правка профиля соискателя вручную (см. также tools/parse_resume.py для PDF)."""
    path, pid = _profile_path(body.profile_id)
    if not os.path.exists(path):
        raise HTTPException(404, "профиль не найден: " + pid)
    data = json.load(open(path, encoding="utf-8"))
    data["title"] = (body.title or "").strip()[:120]
    data["location"] = (body.location or "").strip()[:80]
    data["experience_years"] = max(0, int(body.experience_years or 0))
    pref = data.setdefault("preferences", {})
    pref["salary_net"] = max(0, int(body.salary_net or 0))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"profile_id": pid, "data": data}


# ------------------------------------------------- outbox (локальные черновики)
def _letter_path(user, fname):
    fname = os.path.basename(fname)  # защита от выхода из каталога пользователя
    path = os.path.join(config.OUTBOX_DIR, _uid(user), fname)
    if not os.path.isfile(path):
        raise HTTPException(404, "письмо не найдено")
    return fname, path


def _letter_out(fname, path):
    data = parse_letter(open(path, encoding="utf-8").read())
    meta = data["meta"]
    title = data["title"]
    if not title or title == "Письмо работодателю":  # старый формат — название из вакансии
        title = make_letter_title(meta.get("вакансия", ""), meta.get("работодатель", ""))
    return {"file": fname,
            "title": title,
            "vacancy": meta.get("вакансия", ""), "employer": meta.get("работодатель", ""),
            "url": meta.get("ссылка", ""), "tone": meta.get("тон", ""),
            "created": meta.get("создано", ""), "status": meta.get("статус", ""),
            "body": data["body"], "size": len(data["body"]),
            "modified": os.path.getmtime(path)}


_REGEN_PROMPT = """Ты редактируешь сопроводительные письма работодателям от имени соискателя.
Перепиши письмо так, чтобы оно звучало естественно и по-человечески, а не как нейросеть:
короткие простые предложения, конкретика из резюме и вакансии, никакого канцелярита и
штампов («хотел(а) бы выразить интерес», «в заключение», «команда мечты»), без эмодзи и
восклицательных знаков. Сохрани смысл, тон и структуру (обращение, 2–4 абзаца, подпись).
Верни ТОЛЬКО новый текст письма — без пояснений, заголовков и кавычек."""


@app.get("/api/outbox")
def outbox_list(user=Depends(current_user)):
    user_dir = os.path.join(config.OUTBOX_DIR, _uid(user))
    if not os.path.isdir(user_dir):
        return {"letters": []}
    letters = []
    for fname in sorted(os.listdir(user_dir), reverse=True):
        path = os.path.join(user_dir, fname)
        if not os.path.isfile(path):
            continue
        item = _letter_out(fname, path)
        item["preview"] = item["body"][:160]
        letters.append(item)
    return {"letters": letters}


@app.get("/api/outbox/{fname}")
def outbox_get(fname: str, user=Depends(current_user)):
    fname, path = _letter_path(user, fname)
    return _letter_out(fname, path)


@app.patch("/api/outbox/{fname}")
def outbox_patch(fname: str, body: LetterPatch, user=Depends(current_user)):
    """Переименовать черновик и/или сохранить правки текста (правит сам пользователь)."""
    fname, path = _letter_path(user, fname)
    data = parse_letter(open(path, encoding="utf-8").read())
    title = (body.title or "").strip()
    text = data["body"] if body.body is None else body.body.strip()
    if len(text) < 50:
        raise HTTPException(400, "текст письма слишком короткий (минимум 50 символов)")
    if len(text) > 5000:
        raise HTTPException(400, "текст письма слишком длинный (максимум 5000 символов)")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_letter(title or data["title"], data["meta"], text))
    return _letter_out(fname, path)


@app.post("/api/outbox/{fname}/regenerate")
async def outbox_regenerate(fname: str, user=Depends(current_user)):
    """Перегенерировать текст письма (LLM, человеческий тон). Ничего не отправляется."""
    import litellm
    fname, path = _letter_path(user, fname)
    data = parse_letter(open(path, encoding="utf-8").read())
    meta = data["meta"]
    from ..system_prompt import profile_summary
    user_parts = [f"Вакансия: {meta.get('вакансия', '')}",
                  f"Работодатель: {meta.get('работодатель', '')}",
                  f"Ссылка: {meta.get('ссылка', '')}",
                  f"Тон: {meta.get('тон', 'деловой')}",
                  profile_summary("anastasia"),
                  "Текущий вариант письма:\n" + data["body"]]
    try:
        resp = await litellm.acompletion(
            model=config.model_name(),
            messages=[{"role": "system", "content": _REGEN_PROMPT},
                      {"role": "user", "content": "\n\n".join(p for p in user_parts if p)}],
            temperature=0.7, max_tokens=800, timeout=90)
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        raise HTTPException(502, "не удалось перегенерировать текст: " + str(e)[:200])
    if len(text) < 50:
        raise HTTPException(502, "модель вернула слишком короткий текст — попробуйте ещё раз")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_letter(data["title"], meta, text))
    return _letter_out(fname, path)


# ---------------------------------------------------------------- tracker
@app.get("/api/tracker")
def tracker_list(user=Depends(current_user)):
    return track_response(tool_context=_ctx(user))


@app.post("/api/tracker")
def tracker_update(body: TrackerIn, user=Depends(current_user)):
    res = track_response(body.vacancy_id, body.status, body.note, follow_up=body.follow_up,
                         tool_context=_ctx(user))
    if "error" in res:
        raise HTTPException(400, res["error"])
    return res


# ---------------------------------------------------------------- фронтенд
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
