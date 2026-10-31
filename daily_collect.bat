@echo off
rem Ежедневный инкремент датасета карьерного агента (двойной клик).
rem Тот же эффект: .venv\Scripts\python.exe tools\daily_increment.py
chcp 65001 >nul
title HH Daily Increment
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\daily_increment.py %*
echo.
pause
