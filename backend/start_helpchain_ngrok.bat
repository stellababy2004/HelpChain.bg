@echo off
echo Starting Flask App...
start cmd /k "cd backend && flask run"

timeout /t 5 >nul

echo Starting ngrok tunnel on port 5000...
start cmd /k "ngrok http 5000"
