@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Perekup Discord Bot

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" bot.py
) else (
    python bot.py
)

echo.
echo Бот остановлен. Нажми любую клавишу, чтобы закрыть окно.
pause >nul
