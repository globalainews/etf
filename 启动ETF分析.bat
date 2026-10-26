@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON_EXE=F:\anaconda3\python.exe"
set "WEB_URL=http://127.0.0.1:5010/ranking-app"

echo ========================================
echo ETF Analysis and Ranking Web Launcher
echo ========================================
echo.

if not exist "%PYTHON_EXE%" (
    echo Python was not found: %PYTHON_EXE%
    pause
    exit /b 1
)

if not exist "%CD%\app.py" (
    echo app.py was not found in: %CD%
    pause
    exit /b 1
)

rem Do not accidentally keep using an old Flask process on port 5010.
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 5010 -State Listen -ErrorAction SilentlyContinue) { exit 10 }"
if errorlevel 10 (
    echo Port 5010 is already in use.
    echo Stop the old server with Ctrl+C, then run this file again.
    pause
    exit /b 1
)

echo Starting the current app.py from:
echo %CD%
start "ETF Ranking Web Server" /D "%CD%" "%PYTHON_EXE%" -X utf8 "%CD%\app.py"

echo Waiting for the ranking page...
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(45); do { try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 '%WEB_URL%'; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Milliseconds 500 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
    echo The service did not start within 45 seconds.
    echo Check the ETF Ranking Web Server window for its error output.
    pause
    exit /b 1
)

echo Opening: %WEB_URL%
start "" "%WEB_URL%"
echo.
echo The server is running in a separate window.
pause
