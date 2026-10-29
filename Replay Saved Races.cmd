@echo off
setlocal
cd /d "%~dp0"
if not exist "data\tmp" mkdir "data\tmp"
set "TEMP=%CD%\data\tmp"
set "TMP=%CD%\data\tmp"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing. Follow README.md setup first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\replay_dashboard.py --open-browser
set "result=%ERRORLEVEL%"
echo.
echo Replay viewer stopped. Press any key to close this window.
pause >nul
exit /b %result%
