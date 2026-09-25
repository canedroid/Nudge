@echo off
rem ---------------------------------------------------------------------------
rem glass_ui_without_edges demo - Observer-style HUD reference kit (no edge glow)
rem Boots the reference HUD window (sidebar + screens + tray + settings)
rem with fake data so you can eyeball the look-and-feel.
rem Double-click or run from a terminal.
rem ---------------------------------------------------------------------------
cd /d "%~dp0"

set "PY="
where py >nul 2>nul
if %errorlevel%==0 (
    for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%i"
)
if not defined PY (
    where python >nul 2>nul
    if %errorlevel%==0 set "PY=python"
)
if not defined PY (
    echo [glass_ui_without_edges] Python 3 not found. Install the launcher or add python to PATH.
    pause
    exit /b 1
)

"%PY%" -m glass_ui_without_edges.demo
exit /b 0