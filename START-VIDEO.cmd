@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
    echo Run PREPARE-VIDEO.cmd first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m backend.marketing.launch
if errorlevel 1 pause
