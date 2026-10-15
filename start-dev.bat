@echo off
setlocal

rem Resolve the repository root from this script so it works from any current directory.
set "ROOT=%~dp0"

rem Start the FastAPI app in its own terminal using the backend virtualenv Python directly.
start "AURA Backend" cmd /k "cd /d "%ROOT%backend" && "%ROOT%backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000"

rem Start the existing Vite npm dev script in a separate terminal from the frontend directory.
start "AURA Frontend" cmd /k "cd /d "%ROOT%frontend" && npm run dev"

endlocal
