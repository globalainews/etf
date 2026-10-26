@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ETF ranking video launcher. Optional date: 2026-10-23
set "ROOT=%~dp0"
set "PYTHON=F:\anaconda3\python.exe"
set "WORKDIR=%ROOT%design\etf_rankings"

if not exist "%PYTHON%" goto :missing_python
if not exist "%WORKDIR%\prototype\pipeline.py" goto :missing_pipeline

set "DATE_ARG="
if "%~1"=="" goto :run_pipeline
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

:run_pipeline
pushd "%WORKDIR%"
echo.
echo ================================================
echo ETF ranking video: starting full build
echo Output folder: %WORKDIR%
echo ================================================

if not defined DATE_ARG (
    "%PYTHON%" -X utf8 -m prototype.pipeline --render all --voice auto
) else (
    "%PYTHON%" -X utf8 -m prototype.pipeline --date "%DATE_ARG%" --render all --voice auto
)

set "EXIT_CODE=%ERRORLEVEL%"
popd
if not "%EXIT_CODE%"=="0" goto :build_failed

echo.
echo [DONE] Video: %WORKDIR%\etf_ranking_demo.mp4
echo [DONE] Manifest: %WORKDIR%\prototype\runs\latest\manifest.json
pause
exit /b 0

:missing_python
echo [ERROR] Python not found: %PYTHON%
echo Edit PYTHON in this batch file if the path has changed.
pause
exit /b 1

:missing_pipeline
echo [ERROR] Pipeline not found: %WORKDIR%\prototype\pipeline.py
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
echo [FAILED] Build stopped. Review the error above.
echo Data quality or topic score gates may deliberately stop publication.
pause
exit /b %EXIT_CODE%
