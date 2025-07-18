@echo off
cd /d "C:\Users\Stella Barbarella\OneDrive\Documents\chatGPT\Projet BG\HelpChain\backend"
REM Активирай виртуалната среда, ако имаш:
call venv\Scripts\activate

REM Стартирай Flask приложението
python app.py

REM Ако искаш автоматично да се отвори браузър:
start "" http://127.0.0.1:5000/