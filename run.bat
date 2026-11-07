@echo off
REM Launch Vazhi.
cd /d "%~dp0"
python serve.py %*
if errorlevel 1 (
  echo.
  echo Could not start. Check that Python 3.9+ is installed and on PATH.
  pause
)
