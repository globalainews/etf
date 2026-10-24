@echo off
chcp 65001 >nul
cd /d "D:\claude projects\ETF"

echo ========================================
echo   ETF Price Update (TDX local data)
echo ========================================
echo.
echo Source: TDX .day files (D:\tdx\vipdoc)
echo ETA: ~2 min
echo.

F:\anaconda3\python tdx_price_update.py

echo.
echo ========================================
echo   Done! Press any key to close...
echo ========================================
pause
