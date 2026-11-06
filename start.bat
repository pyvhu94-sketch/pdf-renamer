@echo off
rem Uruchamia PDF Renamer. Przy pierwszym uruchomieniu instaluje potrzebne biblioteki.
cd /d "%~dp0"

if not exist ".venv\installed.ok" (
    echo Pierwsze uruchomienie: instaluje biblioteki, to potrwa okolo minuty...
    python -m venv .venv
    if errorlevel 1 goto nopython
    ".venv\Scripts\python" -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 goto installerror
    echo ok> ".venv\installed.ok"
)

echo PDF Renamer dziala pod adresem http://127.0.0.1:8765
echo Aby zakonczyc, zamknij to okno.
".venv\Scripts\python" app.py
pause
exit /b

:nopython
echo.
echo Nie znaleziono Pythona. Zainstaluj go ze strony https://www.python.org/downloads/
echo i podczas instalacji zaznacz opcje "Add python.exe to PATH". Potem uruchom start.bat ponownie.
pause
exit /b 1

:installerror
echo.
echo Instalacja bibliotek nie powiodla sie. Sprawdz polaczenie z internetem i uruchom start.bat ponownie.
pause
exit /b 1
