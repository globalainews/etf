@echo off
cd /d "D:\claude projects\ETF"

echo ========================================
echo   ETF Fund Scale Trend Analysis
echo ========================================
echo.
echo Starting server at http://127.0.0.1:5010
echo.

start http://127.0.0.1:5010
F:\anaconda3\python app.py

pause
