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
REM Use the shared virtual environment at free-lancing\env
set "VENV_DIR=C:\Users\HP\free-lancing\env"
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
    echo [ERROR] Backend dependencies not installed - fastapi/uvicorn not found in venv.
    echo [ERROR] Run:  pip install -r requirements.txt
    echo.
    exit /b 1
)

REM --- Python 3.12 compatibility check (razorpay requires pkg_resources from setuptools) ---
python -c "import pkg_resources" >nul 2>&1
if not errorlevel 1 goto deps_ready
echo [INFO] Installing setuptools for Python 3.12 compatibility...
python -m pip install setuptools
if errorlevel 1 (
    echo [ERROR] Failed to install setuptools. Please run: pip install setuptools
    echo.
    exit /b 1
)
:deps_ready

REM --- Ensure storage\media is populated with centralized images ---
if not exist "storage\media\hero\hero001.avif" (
    if exist "..\..\frontend\pratikshya-frontend\public\images\hero\hero001.avif" (
        echo [INFO] Syncing centralized images from frontend to storage\media...
        xcopy /E /I /Y /Q "..\..\frontend\pratikshya-frontend\public\images" "storage\media" >nul 2>&1
    )
)

REM --- Environment configuration hint ---
if not exist ".env" (
    echo [INFO] No backend\.env found - starting with built-in defaults.
    echo [INFO] Copy .env.example to .env and set DATABASE_URL and ALLOWED_ORIGINS for local dev.
    echo.
)

REM --- PostgreSQL Pre-flight check ---
python -c "import socket; s = socket.create_connection(('127.0.0.1', 5432), timeout=1); s.close()" >nul 2>&1
if errorlevel 1 (
    echo [WARNING] ============================================================
    echo [WARNING] PostgreSQL is NOT running or unreachable on localhost:5432!
    echo [WARNING] Database queries will fail until PostgreSQL is started.
    echo [WARNING] Please start PostgreSQL service or check DATABASE_URL in .env.
    echo [WARNING] ============================================================
    echo.
) else (
    echo [INFO] PostgreSQL connection verified on localhost:5432.
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
