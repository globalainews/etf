@echo off
setlocal EnableExtensions
set "PROJECT_ROOT=D:\claude projects\ETF"
set "PYTHON=F:\anaconda3\python.exe"

if not exist "%PYTHON%" goto :missing_python
if not exist "%PROJECT_ROOT%\etf.db" goto :missing_db

pushd "%PROJECT_ROOT%"
echo.
echo ================================================
echo ETF Ranking: Build Local Derived Tables
echo ================================================
echo Processing new trading dates and publishing local SQLite results.
echo Raw fund_scale and fund_price tables are read-only.
echo.

"%PYTHON%" -X utf8 -m sync.etf_sync.local_cli --incremental
if errorlevel 1 goto :calculation_failed

"%PYTHON%" -X utf8 -m sync.etf_sync.local_cli --verify
if errorlevel 1 goto :verification_failed

popd
echo.
echo [DONE] Local derived results are published. Run cloud sync next.
pause
exit /b 0

:missing_python
echo [ERROR] Python was not found: %PYTHON%
pause
exit /b 1

:missing_db
echo [ERROR] Database was not found: %PROJECT_ROOT%\etf.db
pause
exit /b 1

:calculation_failed
set "EXIT_CODE=%ERRORLEVEL%"
popd
echo.
echo [FAILED] Local calculation was not published. Do not run cloud sync.
pause
exit /b %EXIT_CODE%

:verification_failed
set "EXIT_CODE=%ERRORLEVEL%"
popd
echo.
echo [FAILED] Local release verification failed. Do not run cloud sync.
pause
exit /b %EXIT_CODE%
