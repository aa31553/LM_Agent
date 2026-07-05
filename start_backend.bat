@echo off
setlocal
cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=8000"
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  set "PYTHON_EXE=python"
)

echo Starting LM Agent backend...
echo API: http://%HOST%:%PORT%/api/v1
echo Docs: http://%HOST%:%PORT%/docs
echo.
"%PYTHON_EXE%" -m uvicorn app.main:app --host %HOST% --port %PORT%

echo.
echo Backend stopped.
pause
