@echo off
REM ============================================================
REM  PRATIKSHYA FASHON - Backend startup [Windows]
REM
REM  Run from the IDE embedded terminal - no external window needed.
REM  Entrypoint : backend\app\main.py  (app object: app.main:app)
REM  Backend URL: http://localhost:8000
REM ============================================================
setlocal
title PRATIKSHYA FASHON - Backend

REM This script now lives inside the backend\ folder.
REM %~dp0 resolves to that folder at runtime.
set "BACKEND_DIR=%~dp0"
set "VENV_DIR=%BACKEND_DIR%.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_ACTIVATE=%VENV_DIR%\Scripts\activate.bat"

if not exist "%BACKEND_DIR%app\main.py" (
    echo [ERROR] Backend entrypoint not found: "%BACKEND_DIR%app\main.py"
    echo [ERROR] This script must live inside the "backend" folder.
    echo.
    exit /b 1
)

REM --- Python / virtual-environment checks ---
if not exist "%VENV_PYTHON%" (
    echo [ERROR] Backend virtual environment not found.
    echo [ERROR] Expected: "%VENV_PYTHON%"
    echo.
    echo To create it, run from this folder:
    echo   python -m venv .venv
    echo   .venv\Scripts\activate
    echo   pip install -r requirements.txt
    echo.
    echo Requirements: Python 3.11 - see backend\README.md for details.
    echo.
    exit /b 1
)

call "%VENV_ACTIVATE%"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment: "%VENV_ACTIVATE%"
    echo [ERROR] Try deleting backend\.venv and recreating it.
    echo.
    exit /b 1
)

cd /d "%BACKEND_DIR%"
if errorlevel 1 (
    echo [ERROR] Could not enter backend directory: "%BACKEND_DIR%"
    echo.
    exit /b 1
)

REM --- Dependency check ---
python -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Backend dependencies not installed - fastapi/uvicorn not found in .venv.
    echo [ERROR] Run:  pip install -r requirements.txt
    echo.
    exit /b 1
)

REM --- Environment configuration hint ---
if not exist ".env" (
    echo [INFO] No backend\.env found - starting with built-in defaults.
    echo [INFO] Copy .env.example to .env and set DATABASE_URL and ALLOWED_ORIGINS for local dev.
    echo.
)

echo ============================================================
echo  PRATIKSHYA FASHON - Backend
echo ============================================================
python --version
echo  Backend : http://localhost:8000
echo  Swagger : http://localhost:8000/docs
echo  Health  : http://localhost:8000/health
echo  Press Ctrl+C to stop.
echo.

uvicorn app.main:app --reload
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] Backend dev server exited with code %EXIT_CODE%.
) else (
    echo Backend dev server stopped.
)
echo.
