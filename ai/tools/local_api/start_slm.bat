@echo off
setlocal
rem Local diagnostic only. The main backend already uses port 8000.
if not defined SLM_PYTHON set "SLM_PYTHON=%~dp0..\..\..\.venv\Scripts\python.exe"
if not exist "%SLM_PYTHON%" (
  echo Python environment missing. Create the repository .venv and follow ai/README.md.
  exit /b 1
)
cd /d "%~dp0"
"%SLM_PYTHON%" -m uvicorn app:app --host 127.0.0.1 --port 8001 --no-access-log
exit /b %ERRORLEVEL%
