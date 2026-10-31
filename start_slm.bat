@echo off
rem 무드월 SLM 서버 실행 + 꺼지면 5초 뒤 자동 재시작
cd /d "%~dp0"
call venv\Scripts\activate.bat
:loop
echo [%date% %time%] start >> server.log
python -m uvicorn app:app --host 127.0.0.1 --port 8000 >> server.log 2>&1
echo [%date% %time%] stopped, restarting in 5s >> server.log
ping -n 6 127.0.0.1 >nul
goto loop
