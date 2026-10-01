@echo off
REM ===  KS ToolBox — double-click to launch  ===
REM First run auto-creates the venv + installs deps; after that it just opens.
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo First run - setting up KS ToolBox ^(one time^)...
    where uv >nul 2>&1 && ( uv venv .venv ) || ( py -3 -m venv .venv 2>nul || python -m venv .venv )
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
)

REM launch the GUI with no console window; the launcher closes immediately
start "" ".venv\Scripts\pythonw.exe" main.py
