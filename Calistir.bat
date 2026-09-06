@echo off
rem Whispera - sanal ortamdaki Python ile konsolsuz baslatir.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "Whispera.py"
) else (
    echo .venv bulunamadi, sistem Python'u deneniyor...
    start "" pythonw "Whispera.py"
)
