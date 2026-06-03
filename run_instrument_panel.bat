@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_instrument_panel.ps1"

if errorlevel 1 (
    echo.
    echo Launcher failed. Press any key to close.
    pause >nul
)

endlocal
