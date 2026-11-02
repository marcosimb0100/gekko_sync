@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "%~dp0run.py"
) else (
    echo Primero crea el entorno: python -m venv .venv
    pause
)
