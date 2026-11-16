# -*- coding: utf-8 -*-
"""Локальная LLM (llama.cpp llama-server + gguf из models/) — работа без интернета.

В local-режиме (config.llm_mode) модель Qwen3.5-4B запускается OpenAI-совместимым
сервером на 127.0.0.1 — весь проект (ADK-агент, сжатие памяти, письма) ходит в неё
как в обычный провайдер: ключ API и сеть не нужны.

llama-server.exe берётся из vendor/llama-cpp: CUDA-сборка при наличии GPU,
иначе CPU-сборка (ноутбук без видеокарты). Переопределение: LLAMA_BINARY=<путь>.

CLI: python -m app.llm_local --status | --start | --stop | --set cloud|local
"""
import json
import os
import subprocess
import time
import urllib.request

from . import config

_PROC = None  # запущенный нами процесс llama-server (stop() гасит только свой)
_PID_FILE = "llama_server.pid"  # в DATA: чтобы --stop гасил и CLI-запущенный сервер


def _pid_path():
    return os.path.join(config.DATA, _PID_FILE)


def _log_path():
    """Вывод llama-server (загрузка модели, ошибки, скорость) — для диагностики."""
    return os.path.join(config.DATA, "llama_server.log")


def _terminate(pid):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=15)
    except Exception:
        pass


def _has_nvidia():
    try:
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=10)
        return r.returncode == 0 and b"NVIDIA" in (r.stdout or b"")
    except Exception:
        return False


def binary_path():
    """Путь к llama-server.exe или None."""
    env_bin = os.environ.get("LLAMA_BINARY", "")
    if env_bin and os.path.exists(env_bin):
        return env_bin
    cuda = os.path.join(config.LLAMA_CPP_DIR, "win-cuda-12.4-x64", "llama-server.exe")
    if os.path.exists(cuda) and _has_nvidia():
        return cuda
    cpu = os.path.join(config.LLAMA_CPP_DIR, "win-cpu-x64", "llama-server.exe")
    return cpu if os.path.exists(cpu) else None


def is_running():
    """Отвечает ли llama-server на /health (вне зависимости, кто его запустил)."""
    url = "http://%s:%d/health" % (config.LOCAL_HOST, config.LOCAL_PORT)
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return json.loads(r.read().decode()).get("status") == "ok"
    except Exception:
        return False


def ensure_running(timeout=180):
    """Запустить llama-server, если не отвечает. Возвращает (ok, сообщение).

    Первый старт грузит gguf ~20-60 с — дожидается готовности /health."""
    if is_running():
        return True, "локальная модель уже запущена"
    exe = binary_path()
    if not exe:
        return False, "не найден llama-server.exe в vendor/llama-cpp"
    if not os.path.exists(config.LOCAL_GGUF):
        return False, "не найдена модель: " + config.LOCAL_GGUF
    global _PROC
    args = [exe, "-m", config.LOCAL_GGUF,
            "--host", config.LOCAL_HOST, "--port", str(config.LOCAL_PORT),
            # общий KV-кэш на все слоты: у каждого агента (координатор + 3 суб-агента)
            # свой системный промпт ~2-3k токенов; при 8192 они вытесняли друг друга и
            # на CPU каждый шаг заново считал весь промпт (минуты на ход)
            "-c", os.environ.get("LLAMA_CTX", "20480"),
            "-np", "4", "--kv-unified",  # слот на агента: префикс промпта переиспользуется
            "--reasoning", "off",   # иначе Qwen3.5 «думает» и всё уходит в reasoning_content
            "--no-warmup"]
    if "cuda" in exe.lower() or os.environ.get("LLAMA_NGL"):
        # выгрузка слоёв на GPU; CPU-сборка флаг не получает (живёт на CPU)
        args += ["-ngl", os.environ.get("LLAMA_NGL", "99")]
    try:
        os.makedirs(config.DATA, exist_ok=True)
        log = open(_log_path(), "w", encoding="utf-8", errors="replace")
        _PROC = subprocess.Popen(args, cwd=config.PROJECT, stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return False, "не удалось запустить llama-server: " + str(e)[:200]
    try:
        os.makedirs(config.DATA, exist_ok=True)
        with open(_pid_path(), "w") as f:
            f.write(str(_PROC.pid))
    except OSError:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_running():
            return True, "локальная модель запущена"
        if _PROC.poll() is not None:
            return False, ("llama-server завершился при запуске (код %s), подробности: %s"
                           % (_PROC.returncode, _log_path()))
        time.sleep(2)
    return False, "модель не поднялась за %d с" % timeout


def stop():
    """Остановить запущенный нами llama-server (чужой не трогаем)."""
    global _PROC
    proc, _PROC = _PROC, None
    if proc is not None and proc.poll() is None:
        _terminate(proc.pid)
    elif is_running():
        # сервер поднят из CLI --start другим процессом — гасим по pid-файлу
        try:
            with open(_pid_path()) as f:
                _terminate(int(f.read().strip()))
        except (OSError, ValueError):
            pass
    try:
        os.remove(_pid_path())
    except OSError:
        pass


def status():
    """Состояние для UI/диагностики."""
    return {"mode": config.llm_mode(), "running": is_running(),
            "url": config.LOCAL_API_BASE, "binary": binary_path(),
            "model": config.LOCAL_GGUF, "managed": _PROC is not None}


def main():
    import sys
    args = sys.argv[1:]
    cmd = args[0] if args else "--status"
    if cmd == "--set" and len(args) > 1:
        config.set_llm_mode(args[1].strip().lower())
        print("режим:", config.llm_mode())
    elif cmd == "--start":
        ok, msg = ensure_running()
        print(("OK: " if ok else "ОШИБКА: ") + msg)
        sys.exit(0 if ok else 1)
    elif cmd == "--stop":
        stop()
        print("остановлено")
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
