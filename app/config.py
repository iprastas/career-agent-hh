# -*- coding: utf-8 -*-
"""
Конфигурация приложения: пути проекта, загрузка .env, имя модели.

Правила секретов: значения из .env попадают только в os.environ и никогда не печатаются.
apply_env() вызывается ДО импорта litellm/ADK (см. agent.py).
"""
import os

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(PROJECT, "data")
APP_DB = os.path.join(DATA, "app.db")          # датасет вакансий (read-only для инструментов)
TRACKER_DB = os.path.join(DATA, "tracker.db")  # локальный трекер откликов (единственная запись)
OUTBOX_DIR = os.path.join(DATA, "outbox")      # локальные черновики писем (никогда не отправляются)
PROFILES_DIR = os.path.join(DATA, "profiles")

_QUOTE_CHARS = ("\"", "'")

PROVIDER_PREFIXES = ("openai/", "groq/", "anthropic/", "bedrock/", "vertex_ai/")


def load_env(path=None):
    """Парсинг .env: split по первому '=', кавычки снимаются. Не source-ить в bash!"""
    env = {}
    p = path or os.path.join(PROJECT, ".env")
    if not os.path.exists(p):
        return env
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in _QUOTE_CHARS:
            v = v[1:-1]
        env[k.strip()] = v
    return env


def apply_env():
    """Экспорт ключей провайдера в окружение (idempotent). Ничего не печатает."""
    env = load_env()
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        if env.get(key):
            os.environ.setdefault(key, env[key])
    if env.get("OPENAI_BASE_URL"):
        # litellm читает OPENAI_API_BASE; оба имени не взаимозаменяемы между версиями
        os.environ.setdefault("OPENAI_API_BASE", env["OPENAI_BASE_URL"])
    return env


def model_name():
    """Имя модели с префиксом провайдера: реестр ADK резолвит по префиксу,
    'голое' имя падает с 'Model <name> not found'."""
    env = load_env()
    m = os.environ.get("MODEL") or env.get("MODEL", "")
    if not m:
        raise RuntimeError("MODEL не задан в .env")
    if not m.startswith(PROVIDER_PREFIXES):
        m = "openai/" + m
    return m
