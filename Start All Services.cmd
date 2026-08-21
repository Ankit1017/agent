@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-all.ps1"
if errorlevel 1 (
    echo.
    echo Service startup failed. Review the message above.
    pause
    exit /b 1
)
echo.
echo All services are ready at http://127.0.0.1:3000
pause
