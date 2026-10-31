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

from ..agent import build_agent, APP_NAME  # noqa: E402
from ..event_protocol import stream_turn  # noqa: E402
from ..tools import (draft_letter, match_profile, search_vacancies,  # noqa: E402
                     skill_gap, salary_stats, track_response)
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
    resp = await _runner().session_service.list_sessions(app_name=APP_NAME, user_id=_uid(user))
    sessions = getattr(resp, "sessions", resp) or []
    out = []
    for s in sessions:
        out.append({"session_id": s.id,
                    "updated_at": getattr(s, "last_update_time", None)})
    return {"sessions": out}


@app.get("/api/sessions/{session_id}/history")
async def history(session_id: str, user=Depends(current_user)):
    s = await _runner().session_service.get_session(app_name=APP_NAME, user_id=_uid(user),
                                                    session_id=session_id)
    if s is None:
        raise HTTPException(404, "сессия не найдена")
    msgs = []
    for ev in s.events:
        if not (ev.content and ev.content.parts):
            continue
        role = "user" if getattr(ev.content, "role", "") == "user" else "agent"
        text = " ".join(p.text for p in ev.content.parts if getattr(p, "text", None))
        if text.strip():
            msgs.append({"role": role, "text": text.strip()})
    return {"session_id": session_id, "messages": msgs}


# ---------------------------------------------------------------- SSE-чат
@app.post("/api/chat/stream")
async def chat_stream(body: ChatIn, user=Depends(current_user)):
    runner = _runner()
    uid = _uid(user)
    if body.session_id:
        s = await runner.session_service.get_session(app_name=APP_NAME, user_id=uid,
                                                     session_id=body.session_id)
        if s is None:
            raise HTTPException(404, "сессия не найдена")
        sid = body.session_id
    else:
        s = await runner.session_service.create_session(app_name=APP_NAME, user_id=uid)
        sid = s.id

    def sse(env):
        return "event: " + env["type"] + "\ndata: " + json.dumps(env, ensure_ascii=False) + "\n\n"

    async def gen():
        yield sse({"type": "session", "session_id": sid})
        try:
            async for env in stream_turn(runner, uid, sid, body.message):
                yield sse(env)
        except Exception as e:  # модель/сеть — не роняем поток молча
            yield sse({"type": "error", "text": "ошибка агента: " + str(e)[:200]})
            yield sse({"type": "done", "tools": [], "elapsed": 0.0, "refused": False})

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


# ---------------------------------------------------------------- outbox (только чтение)
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
        body = open(path, encoding="utf-8").read()
        letters.append({"file": fname, "size": len(body),
                        "modified": os.path.getmtime(path),
                        "preview": body[:160]})
    return {"letters": letters}


@app.get("/api/outbox/{fname}")
def outbox_get(fname: str, user=Depends(current_user)):
    fname = os.path.basename(fname)  # защита от выхода из каталога пользователя
    path = os.path.join(config.OUTBOX_DIR, _uid(user), fname)
    if not os.path.isfile(path):
        raise HTTPException(404, "письмо не найдено")
    return {"file": fname, "text": open(path, encoding="utf-8").read()}


# ---------------------------------------------------------------- tracker
@app.get("/api/tracker")
def tracker_list(user=Depends(current_user)):
    return track_response(tool_context=_ctx(user))


@app.post("/api/tracker")
def tracker_update(body: TrackerIn, user=Depends(current_user)):
    res = track_response(body.vacancy_id, body.status, body.note, tool_context=_ctx(user))
    if "error" in res:
        raise HTTPException(400, res["error"])
    return res


# ---------------------------------------------------------------- фронтенд
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
