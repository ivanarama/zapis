@echo off
setlocal
set PYTHONIOENCODING=utf-8
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo {"schemaVersion":1,"ok":false,"error":{"code":"dependencies","message":"Run PREPARE-VIDEO.cmd in the Zapis folder first."}}
    exit /b 3
)
rem Keep the caller's working directory: relative campaign paths belong to it.
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
"%~dp0.venv\Scripts\python.exe" -m backend.cli %*
exit /b %errorlevel%
