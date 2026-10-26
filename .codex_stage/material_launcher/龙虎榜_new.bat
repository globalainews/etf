@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Produce the daily ETF share-ranking material package only (JSON + 4 HTML pages).
rem Optional date: YYYYMMDD, YYYY-MM-DD, or --date YYYYMMDD / YYYY-MM-DD.
set "PYTHON=F:\anaconda3\python.exe"
set "PRODUCER_ROOT=D:\antigravity"
set "OUTPUT_ROOT=D:\antigravity\ETF"

if not exist "%PYTHON%" goto :missing_python
if not exist "%PRODUCER_ROOT%\etf_longhubang\cli.py" goto :missing_producer

set "DATE_ARG="
if "%~1"=="" goto :run_producer
if /I "%~1"=="--date" (
    if "%~2"=="" goto :missing_date
    if not "%~3"=="" goto :usage
    set "DATE_ARG=%~2"
) else (
    if not "%~2"=="" goto :usage
    set "DATE_ARG=%~1"
)
call :normalize_date "%DATE_ARG%"
if errorlevel 1 goto :invalid_date

:run_producer
pushd "%PRODUCER_ROOT%"
echo.
echo ================================================
echo ETF daily share-ranking material: starting build
echo Output folder: %OUTPUT_ROOT%
echo Package: JSON + 4 HTML pages (no video rendering)
echo ================================================

if not defined DATE_ARG (
    "%PYTHON%" -X utf8 -m etf_longhubang.cli --output-root "%OUTPUT_ROOT%"
) else (
    "%PYTHON%" -X utf8 -m etf_longhubang.cli --date "%DATE_ARG%" --output-root "%OUTPUT_ROOT%"
)

set "EXIT_CODE=%ERRORLEVEL%"
popd
if not "%EXIT_CODE%"=="0" goto :build_failed

echo.
echo [DONE] Producer completed. Review the JSON status above.
echo        ready = material package; skipped = _SKIP.json receipt.
echo        Output root: %OUTPUT_ROOT%
pause
exit /b 0

:missing_python
echo [ERROR] Python not found: %PYTHON%
pause
exit /b 1

:missing_producer
echo [ERROR] Material producer not found: %PRODUCER_ROOT%\etf_longhubang\cli.py
pause
exit /b 1

:missing_date
echo [ERROR] --date requires a date value, for example: --date 2026-11-22
pause
exit /b 2

:invalid_date
echo [ERROR] Invalid date format: %DATE_ARG%
echo Use YYYYMMDD or YYYY-MM-DD, for example: 20261122 or 2026-11-22
pause
exit /b 2

:usage
echo [ERROR] Usage: %~nx0 [YYYYMMDD^|YYYY-MM-DD] or %~nx0 --date YYYYMMDD
pause
exit /b 2

:normalize_date
set "DATE_ARG=%~1"
if "%DATE_ARG:~4,1%"=="-" exit /b 0
if "%DATE_ARG:~7,1%"=="" exit /b 1
if not "%DATE_ARG:~8,1%"=="" exit /b 1
set "DATE_ARG=%DATE_ARG:~0,4%-%DATE_ARG:~4,2%-%DATE_ARG:~6,2%"
exit /b 0

:build_failed
echo.
echo [FAILED] Material package was not published.
echo Check the source/data message above. source_unavailable and insufficient_data create a _SKIP receipt for scheduler alerts.
pause
exit /b %EXIT_CODE%
