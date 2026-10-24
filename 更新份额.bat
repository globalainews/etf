@echo off
chcp 65001 >nul
cd /d "D:\claude projects\ETF"

echo ========================================
echo   ETF Scale Update (SSE + SZSE)
echo ========================================
echo.
echo Target: all missing dates (auto-detected)
echo Source: SSE / SZSE via akshare
echo ETA: ~1 min per missing date
echo.

F:\anaconda3\python daily_update.py --scale-only

echo.
echo ========================================
echo   Done! Press any key to close...
echo ========================================
pause
