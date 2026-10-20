@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher "py" not found. Install 64-bit Python 3.11+ from python.org first.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv ...
    py -3 -m venv .venv || (pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt pytest || (pause & exit /b 1)
echo.
echo Running tests ...
".venv\Scripts\python.exe" -m pytest -q
echo.
echo Done. Start the app with start_microscope.bat
pause
