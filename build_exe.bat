@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No s'ha trobat l'entorn virtual .venv.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
python -m pip install --quiet pyinstaller
python -m PyInstaller --noconfirm --distpath dist\windows --workpath "%TEMP%\Erola-build-%RANDOM%-%RANDOM%" GestorTracabilitat.spec
if errorlevel 1 (
    echo Error generant l'executable.
    pause
    exit /b 1
)

echo.
echo Executable generat a: dist\windows\erola producció.exe
pause
