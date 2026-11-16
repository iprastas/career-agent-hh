# -*- coding: utf-8 -*-
"""Переключатель облако ↔ локальная модель: режим, окружение, эндпоинты (без сети).

Контракты: выбор режима живёт в DATA/llm_mode.json и переживает перезапуск;
local-режим подставляет локальный llama-server в litellm/ADK (ключ и сеть не нужны);
возврат в облако снимает локальные override'ы окружения; агент пересобирается
(сбрасывается _RUNNER) при каждом переключении.
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

from app import config, llm_local
from app.server import store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # изоляция окружения: правки os.environ не утекают из теста
    monkeypatch.setattr(os, "environ", dict(os.environ))
    for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "MODEL", "LLM_MODE",
              "LLAMA_BINARY", "NO_PROXY", "no_proxy"):
        os.environ.pop(k, None)
    monkeypatch.setattr(config, "DATA", str(tmp_path))
    monkeypatch.setattr(config, "load_env", lambda path=None: {})  # без .env — детерминизм
    monkeypatch.setattr(store, "USERS_DB", str(tmp_path / "users.db"))
    # границы с реальным миром замоканы: сеть и процессы тесты не трогают
    running = {"on": False}

    def fake_ensure(timeout=180):
        running["on"] = True
        return True, "модель запущена"

    monkeypatch.setattr(llm_local, "is_running", lambda: running["on"])
    monkeypatch.setattr(llm_local, "ensure_running", fake_ensure)
    monkeypatch.setattr(llm_local, "binary_path", lambda: "vendor/llama-cpp/mock/llama-server.exe")
    import app.server.main as main
    monkeypatch.setattr(main, "_RUNNER", None)
    return TestClient(main.app)


def _reg(client, email="a@test.ru"):
    r = client.post("/api/register", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


class TestLlmMode:
    def test_default_is_cloud(self, client):
        h = _reg(client)
        assert config.llm_mode() == config.LLM_MODE_CLOUD
        s = client.get("/api/llm/status", headers=h).json()
        assert s["mode"] == "cloud" and s["running"] is False

    def test_switch_to_local(self, client):
        h = _reg(client)
        r = client.post("/api/llm/mode", json={"mode": "local"}, headers=h)
        assert r.status_code == 200 and r.json()["ok"] is True
        assert config.llm_mode() == "local"
        assert config.model_name() == "openai/" + config.LOCAL_MODEL_ID
        assert os.environ["OPENAI_API_BASE"] == config.LOCAL_API_BASE
        assert os.environ["OPENAI_API_KEY"] == config.LOCAL_API_KEY
        assert config.LOCAL_HOST in os.environ["NO_PROXY"]  # localhost — без прокси
        s = client.get("/api/llm/status", headers=h).json()
        assert s["mode"] == "local" and s["running"] is True

    def test_choice_persists_in_data(self, client, tmp_path):
        h = _reg(client)
        client.post("/api/llm/mode", json={"mode": "local"}, headers=h)
        with open(tmp_path / "llm_mode.json", encoding="utf-8") as f:
            assert json.load(f)["mode"] == "local"
        assert config.llm_mode() == "local"  # и читается обратно

    def test_switch_back_to_cloud_clears_local_env(self, client):
        h = _reg(client)
        client.post("/api/llm/mode", json={"mode": "local"}, headers=h)
        r = client.post("/api/llm/mode", json={"mode": "cloud"}, headers=h)
        assert r.status_code == 200
        assert config.llm_mode() == "cloud"
        assert os.environ.get("OPENAI_API_BASE") is None  # локальный override снят
        assert os.environ.get("OPENAI_API_KEY") is None
        # без MODEL в .env облачный model_name() честно падает, а не молча берёт локальную
        with pytest.raises(RuntimeError):
            config.model_name()

    def test_local_overrides_cloud_base_url(self, client, monkeypatch):
        """Регрессия «aitunnel: Недействительный токен»: litellm берёт OPENAI_BASE_URL
        раньше OPENAI_API_BASE, а сам подгружает его из .env. В local-режиме оба имени
        и явные kwargs должны указывать на llama-server."""
        monkeypatch.setattr(config, "load_env", lambda path=None: {
            "OPENAI_API_KEY": "sk-cloud", "OPENAI_BASE_URL": "https://api.cloud.example/v1",
            "MODEL": "vendor/model"})
        config.apply_env()  # старт в облаке: облачный шлюз в окружении
        assert os.environ["OPENAI_BASE_URL"] == "https://api.cloud.example/v1"
        h = _reg(client)
        client.post("/api/llm/mode", json={"mode": "local"}, headers=h)
        assert os.environ["OPENAI_BASE_URL"] == config.LOCAL_API_BASE
        assert os.environ["OPENAI_API_BASE"] == config.LOCAL_API_BASE
        assert config.llm_kwargs() == {"api_base": config.LOCAL_API_BASE,
                                       "api_key": config.LOCAL_API_KEY}
        from app.agent import build_agent
        agent = build_agent()
        for a in [agent] + list(agent.sub_agents):
            assert a.model._additional_args["api_base"] == config.LOCAL_API_BASE
        # обратно в облако — шлюз и ключ из .env возвращаются
        client.post("/api/llm/mode", json={"mode": "cloud"}, headers=h)
        assert os.environ["OPENAI_BASE_URL"] == "https://api.cloud.example/v1"
        assert os.environ["OPENAI_API_KEY"] == "sk-cloud"
        assert config.llm_kwargs() == {}

    def test_bad_mode_422(self, client):
        h = _reg(client)
        r = client.post("/api/llm/mode", json={"mode": "gpu"}, headers=h)
        assert r.status_code == 422

    def test_requires_auth(self, client):
        assert client.get("/api/llm/status").status_code == 401
        assert client.post("/api/llm/mode", json={"mode": "local"}).status_code == 401

    def test_local_start_failure_reported(self, client, monkeypatch):
        monkeypatch.setattr(llm_local, "ensure_running",
                            lambda timeout=180: (False, "не найден llama-server.exe"))
        h = _reg(client)
        r = client.post("/api/llm/mode", json={"mode": "local"}, headers=h)
        assert r.status_code == 200 and r.json()["ok"] is False
        assert "llama-server" in r.json()["message"]

    def test_env_llm_mode_fallback(self, client):
        # без файла режим берётся из окружения (для run_server_local.bat и тестов)
        os.environ["LLM_MODE"] = "local"
        assert config.llm_mode() == "local"


class TestLlmTimeout:
    def test_local_allows_slow_model(self, client):
        config.set_llm_mode("local")
        assert config.llm_timeout(60) >= 300
        config.set_llm_mode("cloud")
        assert config.llm_timeout(60) == 60

    def test_set_llm_mode_validates(self, client):
        with pytest.raises(ValueError):
            config.set_llm_mode("quantum")


class TestBinaryPath:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        exe = tmp_path / "llama-server.exe"
        exe.write_bytes(b"stub")
        monkeypatch.setenv("LLAMA_BINARY", str(exe))
        assert llm_local.binary_path() == str(exe)

    def test_none_when_nothing_installed(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LLAMA_BINARY", raising=False)
        monkeypatch.setattr(config, "LLAMA_CPP_DIR", str(tmp_path))
        monkeypatch.setattr(llm_local, "_has_nvidia", lambda: False)
        assert llm_local.binary_path() is None
