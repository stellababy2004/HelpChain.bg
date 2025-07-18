@echo off
cd /d "%~dp0backend"

REM Активирай виртуалната среда (ако съществува)
IF EXIST venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Стартирай Python и създай таблицата
echo Creating database...
python -c "from app import db, app; with app.app_context(): db.create_all()"

REM Стартирай Flask приложението
echo Starting Flask server...
python app.py
