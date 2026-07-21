@echo off
setlocal EnableExtensions

set "SERVICE_DIR=%~dp0"
for %%I in ("%SERVICE_DIR%..\..") do set "PROJECT_ROOT=%%~fI"

if "%EMBEDDING_HOST%"=="" set "EMBEDDING_HOST=127.0.0.1"
if "%EMBEDDING_PORT%"=="" set "EMBEDDING_PORT=1234"
if not "%~1"=="" set "EMBEDDING_PORT=%~1"

set "PYTHON_EXE=%SERVICE_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "PYTHONPATH=%PROJECT_ROOT%;%PYTHONPATH%"

echo ============================================================
echo LM Agent Embedding Service
echo API: http://%EMBEDDING_HOST%:%EMBEDDING_PORT%
echo Swagger: http://%EMBEDDING_HOST%:%EMBEDDING_PORT%/docs
echo ReDoc: http://%EMBEDDING_HOST%:%EMBEDDING_PORT%/redoc
echo Model: %EMBEDDING_MODEL_PATH%
echo Press Ctrl+C to stop the service.
echo ============================================================

cd /d "%PROJECT_ROOT%"
"%PYTHON_EXE%" -m uvicorn services.embedding_service.app:app --host "%EMBEDDING_HOST%" --port "%EMBEDDING_PORT%"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Embedding Service stopped with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
