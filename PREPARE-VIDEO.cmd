@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements-video.txt
if errorlevel 1 exit /b 1
call npm.cmd ci --prefix video --no-audit --no-fund
if errorlevel 1 exit /b 1
echo.
echo Ready. Run START-VIDEO.cmd
pause
