@echo off
rem Buduje dist\PDF-Renamer.exe (jeden plik, nie wymaga Pythona na komputerze uzytkownika).
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" python -m venv .venv
".venv\Scripts\python" -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

".venv\Scripts\pyinstaller" --noconfirm --clean --onefile --console ^
    --name PDF-Renamer ^
    --collect-submodules uvicorn ^
    --exclude-module playwright ^
    app.py
