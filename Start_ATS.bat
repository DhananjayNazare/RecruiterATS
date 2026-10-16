@echo off
setlocal
cd /d "%~dp0"
title Recruiter ATS - Production Server

echo ===============================================
echo        Recruiter ATS - Production Start
echo ===============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Production environment not found.
    echo [INFO] Running first-time setup...
    call setup_production_windows.bat
    if errorlevel 1 (
        echo.
        echo [ERROR] Setup failed. Please review the messages above.
        pause
        exit /b 1
    )
)

if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] Creating .env from .env.example...
        copy /Y ".env.example" ".env" >nul
        echo [WARNING] Review .env before production use, especially SECRET_KEY and SMTP settings.
    ) else (
        echo [WARNING] .env file not found. The application may not start securely.
    )
)

echo.
echo [INFO] Running production configuration check...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" production_check.py
) else (
    python production_check.py
)

if errorlevel 1 (
    echo.
    echo [ERROR] Production check failed. Fix the reported issues before starting ATS.
    pause
    exit /b 1
)

echo.
echo [INFO] Starting Recruiter ATS with Waitress WSGI...
echo [INFO] Keep this window open while the ATS is running.
echo [INFO] Open: https://localhost:5001  (accept the one-time browser warning for the self-signed certificate)
echo.
call run_production_windows.bat

endlocal
