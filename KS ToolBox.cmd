@echo off
REM ===  KS ToolBox — Double-click to launch  ===
REM Automatically detects Python, sets up a local virtual environment on first run,
REM and launches the application. Works seamlessly on any computer.
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM 1. Verify if an existing .venv is functional on this machine
set "VENV_OK=0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import customtkinter, yt_dlp" >nul 2>&1 && set "VENV_OK=1"
)

REM 2. If .venv is missing or non-functional, set it up automatically
if "!VENV_OK!"=="0" (
    echo [KS ToolBox] Setting up virtual environment and dependencies...
    
    REM Detect Python interpreter
    set "PY_CMD="
    where uv >nul 2>&1 && set "PY_CMD=uv"
    if "!PY_CMD!"=="" (
        py -3 -V >nul 2>&1 && set "PY_CMD=py -3"
    )
    if "!PY_CMD!"=="" (
        python -V >nul 2>&1 && set "PY_CMD=python"
    )
    
    if "!PY_CMD!"=="" (
        echo.
        echo [ERROR] Python 3 was not found on this computer.
        echo Please install Python 3.10+ from https://www.python.org or run:
        echo   winget install Python.Python.3.11
        echo.
        pause
        exit /b 1
    )
    
    if "!PY_CMD!"=="uv" (
        echo Using uv for fast environment setup...
        uv venv --clear .venv
        uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
    ) else (
        echo Creating virtual environment...
        !PY_CMD! -m venv --clear .venv
        ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
        ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    )
)

REM 3. Verify .venv is ready
if not exist ".venv\Scripts\pythonw.exe" (
    echo [ERROR] Failed to set up Python virtual environment.
    pause
    exit /b 1
)

REM 4. Launch the application
start "" ".venv\Scripts\pythonw.exe" main.py
