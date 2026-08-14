@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install -q -r requirements.txt

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Создал .env — впиши PIKABU_BOT_TOKEN от нового бота из @BotFather.
  notepad ".env"
  exit /b 1
)

python bot.py %*
