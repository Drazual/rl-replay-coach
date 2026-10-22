@echo off
rem Double-click to start RL Coach. The first run sets things up (about a minute).
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" goto run
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (echo Python is not installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH". & pause & exit /b 1)
echo Setting up RL Coach for the first time...
%PY% -m venv .venv || (pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install ".[app]" || (echo Setup failed. & pause & exit /b 1)
:run
start "" ".venv\Scripts\pythonw.exe" -m rlcoach.app
