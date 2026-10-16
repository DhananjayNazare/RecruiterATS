@echo off
cd /d %~dp0
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if exist .env set PYTHONUNBUFFERED=1
python app.py
pause
