@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Контракты ИП - SQLite LAB (НЕ РАБОЧАЯ ВЕРСИЯ)
where python >nul 2>nul
if errorlevel 1 (
  echo Python не найден. Установите Python 3.11 или новее.
  pause
  exit /b 1
)
start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://localhost:8766/'"
echo ТЕСТОВАЯ SQLite версия. Действующее приложение на порту 8765 не изменяется.
python server.py
if errorlevel 1 pause
