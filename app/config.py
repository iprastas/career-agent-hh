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

# --- локальный режим (без интернета): llama.cpp llama-server + gguf из models/ ---
LLM_MODE_CLOUD = "cloud"
LLM_MODE_LOCAL = "local"
LOCAL_MODEL_ID = "qwen3.5-4b-local"          # имя модели для litellm/ADK (llama-server не валидирует)
LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8080
LOCAL_API_BASE = "http://%s:%d/v1" % (LOCAL_HOST, LOCAL_PORT)
LOCAL_API_KEY = "sk-local-offline"           # litellm требует непустой ключ; запросы идут на localhost
LOCAL_GGUF = os.path.join(PROJECT, "models", "Qwen3.5-4B-Q4_K_M.gguf")
LLAMA_CPP_DIR = os.path.join(PROJECT, "vendor", "llama-cpp")
_MODE_FILE = "llm_mode.json"                 # в DATA: выбор режима переживает перезапуск


def llm_mode():
    """cloud|local: выбор из UI (DATA/llm_mode.json) > LLM_MODE из окружения > cloud."""
    import json
    try:
        with open(os.path.join(DATA, _MODE_FILE), encoding="utf-8") as f:
            m = (json.load(f).get("mode") or "").strip().lower()
            if m in (LLM_MODE_CLOUD, LLM_MODE_LOCAL):
                return m
    except Exception:
        pass
    m = os.environ.get("LLM_MODE", "").strip().lower()
    return LLM_MODE_LOCAL if m == LLM_MODE_LOCAL else LLM_MODE_CLOUD


def set_llm_mode(mode):
    """Запомнить режим (DATA/llm_mode.json) и обновить окружение под него."""
    import json
    if mode not in (LLM_MODE_CLOUD, LLM_MODE_LOCAL):
        raise ValueError("режим должен быть 'cloud' или 'local'")
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, _MODE_FILE), "w", encoding="utf-8") as f:
        json.dump({"mode": mode}, f)
    apply_env()


def llm_timeout(base=60):
    """Таймаут запроса к LLM: локальная CPU-модель медленнее облачной."""
    return max(base, 300) if llm_mode() == LLM_MODE_LOCAL else base


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
    """Экспорт ключей провайдера в окружение (idempotent). Ничего не печатает.

    В local-режиме вместо провайдера подставляется локальный llama-server:
    ключ и интернет не нужны. Свои же локальные override'ы при возврате в
    облако снимаются, чтобы не перекрыть настройки .env."""
    env = load_env()
    if llm_mode() == LLM_MODE_LOCAL:
        os.environ["OPENAI_API_BASE"] = LOCAL_API_BASE
        os.environ["OPENAI_API_KEY"] = LOCAL_API_KEY
        # localhost не должен ходить через прокси: на части машин системный прокси
        # (реестр Windows) перехватывает запросы к llama-server и ломает httpx
        for key in ("NO_PROXY", "no_proxy"):
            hosts = [h.strip() for h in os.environ.get(key, "").split(",") if h.strip()]
            for host in (LOCAL_HOST, "localhost"):
                if host not in hosts:
                    hosts.append(host)
            os.environ[key] = ",".join(hosts)
        return env
    for key, ours in (("OPENAI_API_BASE", LOCAL_API_BASE), ("OPENAI_API_KEY", LOCAL_API_KEY)):
        if os.environ.get(key) == ours:
            del os.environ[key]
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
    if llm_mode() == LLM_MODE_LOCAL:
        return "openai/" + LOCAL_MODEL_ID
    env = load_env()
    m = os.environ.get("MODEL") or env.get("MODEL", "")
    if not m:
        raise RuntimeError("MODEL не задан в .env")
    if not m.startswith(PROVIDER_PREFIXES):
        m = "openai/" + m
    return m
