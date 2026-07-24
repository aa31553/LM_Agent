@echo off
setlocal
cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=5173"
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  set "PYTHON_EXE=python"
)

echo Starting LM Agent test frontend...
echo Frontend: http://%HOST%:%PORT%/
echo Backend expected at: http://127.0.0.1:8000/api/v1
echo.
cd /d "%~dp0frontend"
"%PYTHON_EXE%" -m http.server %PORT% --bind %HOST%

echo.
echo Frontend stopped.
pause
