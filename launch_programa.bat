@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No s'ha trobat l'entorn virtual .venv.
    echo Crea'l primer amb: python -m venv .venv
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
python app.py
