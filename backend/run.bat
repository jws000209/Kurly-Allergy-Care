@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto failed
)
echo Starting backend at http://127.0.0.1:8000
echo Startup may take a moment while the product catalog is loaded.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto failed
echo Backend stopped.
pause
exit /b 0

:failed
echo.
echo Backend could not start or exited with an error.
echo Read the error above. If port 8000 is already in use, keep the existing server.
pause
exit /b 1
