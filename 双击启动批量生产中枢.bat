@echo off
chcp 65001 >nul
title ComfyUI Batch Worker
cd /d "%~dp0" || (echo [ERROR] Cannot enter project directory.& pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Missing local Python environment: .venv\Scripts\python.exe
    echo Restore the local environment before starting.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -B -X utf8 -c "import json,urllib.request,sys; sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8999/api/prompt_lab/state',timeout=3)).get('success') else 1)" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] ComfyUI Batch Worker is already running. Opening the page.
    start "" "http://127.0.0.1:8999/"
    exit /b 0
)
".venv\Scripts\python.exe" -B -X utf8 -c "import socket,sys; s=socket.socket(); s.settimeout(2); sys.exit(0 if s.connect_ex(('127.0.0.1',8999))==0 else 1)" >nul 2>&1
if not errorlevel 1 (
    echo [ERROR] Port 8999 is being used by another program. Close that program first.
    pause
    exit /b 1
)
echo Open http://127.0.0.1:8999 after the server starts.
echo Rendering remains paused until you click Start in the page.
".venv\Scripts\python.exe" -B -X utf8 batch_hub_server.py
if errorlevel 1 echo [ERROR] Server exited. Review the error above.
pause
