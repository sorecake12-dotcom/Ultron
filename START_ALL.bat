@echo off
title ULTRON -- Full System Launch (Main AI Engine + Wake Word Service)
color 0A

cd /d "%~dp0"

echo ===================================================
echo   ULTRON AI -- Starting Full System Launch...
echo ===================================================
echo.

REM ── Check Python installation ──────────────────────────────────────────
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python was not found on this PC.
    echo.
    echo Please install Python 3.10 or newer from https://python.org
    echo During install, make sure to check "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

REM ── Check first-time setup status ──────────────────────────────────────
if not exist ".ultron_setup_complete" (
    echo First-time launch detected. Running setup...
    echo.
    python ULTRON_SETUP.py
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo Setup failed. Check the messages above.
        pause
        exit /b %ERRORLEVEL%
    )
)

REM ── 1. Start Wake Word Listener service in background window ───────────
echo [1/2] Launching Wake Word Listener ("wake up ultron")...
start "ULTRON Wake Word Listener" python wake_service.py

timeout /t 2 /nobreak >nul

REM ── 2. Launch Main ULTRON Assistant Engine ─────────────────────────────
echo [2/2] Launching Main ULTRON AI Engine...
echo.
python main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ULTRON closed with exit code %ERRORLEVEL%.
    pause
)
