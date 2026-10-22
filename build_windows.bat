@echo off
rem Builds dist\RLCoach.exe, a single file you can double-click or put on your desktop.
cd /d "%~dp0"
py -3 -m venv .build-venv
".build-venv\Scripts\python.exe" -m pip install --upgrade pip
".build-venv\Scripts\python.exe" -m pip install ".[app]" pyinstaller
".build-venv\Scripts\pyinstaller.exe" --noconfirm --onefile --windowed --name RLCoach --add-data "rlcoach\ui;rlcoach\ui" --collect-all subtr_actor rlcoach_app.py
echo.
echo Done: dist\RLCoach.exe
pause
