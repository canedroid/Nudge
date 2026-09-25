@echo off
rem ---------------------------------------------------------------------------
rem Nodify - launch the overlay from the project virtual environment.
rem ---------------------------------------------------------------------------
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [nodify] .venv not found. Create it with:
    echo     py -3.13 -m venv .venv
    echo     .venv\Scripts\python -m pip install -e ".[dev]"
    exit /b 1
)

".venv\Scripts\python.exe" -m nodify %*
exit /b %errorlevel%
