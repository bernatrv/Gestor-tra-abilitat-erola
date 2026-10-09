@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No s'ha trobat l'entorn virtual .venv.
    echo Crea'l primer amb: python -m venv .venv
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

echo =====================================================
echo Verificant el programa...
echo =====================================================
python -m pytest -q
if errorlevel 1 (
    echo.
    echo ERROR: Les proves han fallat.
    pause
    exit /b 1
)

echo.
python -m compileall src app.py
if errorlevel 1 (
    echo.
    echo ERROR: Hi ha errors de compilacio.
    pause
    exit /b 1
)

echo.

echo =====================================================
echo Tot correcte: la lògica i la compilacio passen.
echo =====================================================
pause
