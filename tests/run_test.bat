@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=camera"

if /i "%TARGET%"=="camera" (
  set "SCRIPT=tests\test_camera.py"
) else if /i "%TARGET%"=="detection" (
  set "SCRIPT=tests\test_detection.py"
) else if /i "%TARGET%"=="control" (
  set "SCRIPT=tests\test_control.py"
) else (
  echo Usage: tests\run_test.bat ^<camera^|detection^|control^>
  exit /b 2
)

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%\.."

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "%SCRIPT%"
  exit /b %errorlevel%
)

py -3 --version >nul 2>nul
if errorlevel 1 (
  python "%SCRIPT%"
) else (
  py -3 "%SCRIPT%"
)

endlocal

