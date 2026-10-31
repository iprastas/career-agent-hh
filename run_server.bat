@echo off
rem Запуск карьерного агента: сервер + открытие интерфейса в браузере.
chcp 65001 >nul
title Карьерный агент hh.ru
cd /d "%~dp0"
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app.server.main:app --host 127.0.0.1 --port 8000
pause
