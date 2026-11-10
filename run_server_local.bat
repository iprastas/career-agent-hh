@echo off
rem Запуск карьерного агента в ЛОКАЛЬНОМ режиме (без интернета):
rem llama.cpp llama-server + Qwen3.5-4B из models/ на 127.0.0.1:8080.
rem На ноутбуке без видеокарты используется CPU-сборка (vendor/llama-cpp/win-cpu-x64).
chcp 65001 >nul
title Карьерный агент hh.ru (локальная модель)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m app.llm_local --set local
".venv\Scripts\python.exe" -m app.llm_local --start
if errorlevel 1 (
  echo Не удалось запустить локальную модель — подробности выше.
  pause
  exit /b 1
)
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app.server.main:app --host 127.0.0.1 --port 8000
pause
