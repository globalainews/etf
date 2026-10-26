@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PROJECT_ROOT=D:\claude projects\ETF"
set "PYTHON=F:\anaconda3\python.exe"
set "CREDENTIAL_FILE=%USERPROFILE%\.etf-cloudbase.env"

if not exist "%PYTHON%" goto :missing_python
if not exist "%PROJECT_ROOT%\etf.db" goto :missing_db

rem Prefer existing environment variables; otherwise load an external credential file.
if not defined CLOUDBASE_ENV_ID call :load_credentials
if not defined CLOUDBASE_API_KEY call :load_credentials
if not defined CLOUDBASE_ENV_ID goto :missing_credentials
if not defined CLOUDBASE_API_KEY goto :missing_credentials

pushd "%PROJECT_ROOT%"
echo.
echo ================================================
echo ETF Ranking: Sync Published Batch to CloudBase
echo ================================================
echo A read-only preflight runs before any CloudBase write.
echo.

"%PYTHON%" -X utf8 -m sync.etf_sync.local_cli --verify
if errorlevel 1 goto :local_not_ready

"%PYTHON%" -X utf8 -m sync.etf_sync.cli --dry-run
if errorlevel 1 goto :dry_run_failed

echo.
set /p "CONFIRM=Preflight passed. Type Y then Enter to sync to CloudBase: "
if /I not "%CONFIRM%"=="Y" goto :cancelled

"%PYTHON%" -X utf8 -m sync.etf_sync.cli
if errorlevel 1 goto :sync_failed

popd
echo.
echo [DONE] Cloud publish pointer updated. Recompile in WeChat DevTools.
pause
exit /b 0

:load_credentials
if not exist "%CREDENTIAL_FILE%" exit /b 0
for /f "usebackq tokens=1,* delims==" %%A in ("%CREDENTIAL_FILE%") do (
  if /I "%%A"=="CLOUDBASE_ENV_ID" set "CLOUDBASE_ENV_ID=%%B"
  if /I "%%A"=="CLOUDBASE_API_KEY" set "CLOUDBASE_API_KEY=%%B"
  if /I "%%A"=="CLOUDBASE_TIMEOUT_SECONDS" set "CLOUDBASE_TIMEOUT_SECONDS=%%B"
  if /I "%%A"=="CLOUDBASE_MAX_RETRIES" set "CLOUDBASE_MAX_RETRIES=%%B"
  if /I "%%A"=="CLOUDBASE_BATCH_SIZE" set "CLOUDBASE_BATCH_SIZE=%%B"
)
exit /b 0

:missing_python
echo [ERROR] Python was not found: %PYTHON%
pause
exit /b 1

:missing_db
echo [ERROR] Database was not found: %PROJECT_ROOT%\etf.db
pause
exit /b 1

:missing_credentials
echo [ERROR] CloudBase credentials were not found.
echo Set CLOUDBASE_ENV_ID and CLOUDBASE_API_KEY in Windows environment variables,
echo or create this external file with two KEY=VALUE lines:
echo %CREDENTIAL_FILE%
echo CLOUDBASE_ENV_ID=your-cloud-environment-id
echo CLOUDBASE_API_KEY=your-server-api-key
pause
exit /b 1

:local_not_ready
set "EXIT_CODE=%ERRORLEVEL%"
popd
echo [FAILED] Local release verification failed. No CloudBase write was sent.
pause
exit /b %EXIT_CODE%

:dry_run_failed
set "EXIT_CODE=%ERRORLEVEL%"
popd
echo [FAILED] Cloud sync preflight failed. No CloudBase write was sent.
pause
exit /b %EXIT_CODE%

:cancelled
popd
echo [CANCELLED] No CloudBase write was sent.
pause
exit /b 0

:sync_failed
set "EXIT_CODE=%ERRORLEVEL%"
popd
echo [FAILED] Cloud sync did not complete. The previous published batch remains active.
pause
exit /b %EXIT_CODE%
