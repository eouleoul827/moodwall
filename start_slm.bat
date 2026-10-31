@echo off
rem Run the SLM API and restart it 5 seconds after it exits.
cd /d "%~dp0"
call venv\Scripts\activate.bat

:loop
echo [%date% %time%] start >> server.log
python -m uvicorn app:app --host 127.0.0.1 --port 8000 >> server.log 2>&1
ping -n 6 127.0.0.1 >nul
goto loop
