@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo The Python environment is missing.
    echo Follow the installation steps in README.md first.
    pause
    exit /b 1
)
start "CNC Microscope" ".venv\Scripts\pythonw.exe" "run_microscope.py"

