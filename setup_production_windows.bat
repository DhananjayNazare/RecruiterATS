@echo off
setlocal
cd /d %~dp0
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not exist .env copy /Y .env.example .env >nul
python -c "import secrets; print('Generated secret:', secrets.token_urlsafe(48))"
echo.
echo Production setup complete.
echo Edit .env and set ARS_SECRET_KEY to the generated secret.
echo Start production WSGI server with: waitress-serve --host=0.0.0.0 --port=5001 wsgi:app
pause
