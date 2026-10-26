@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON_EXE=F:\anaconda3\python.exe"
set "WEB_URL=http://127.0.0.1:5010/ranking-app"

echo ========================================
echo   ETF分析 ^| 龙虎榜 Web 启动器
echo ========================================
echo.

if not exist "%PYTHON_EXE%" (
    echo 未找到 Python：%PYTHON_EXE%
    echo 请修改本 bat 文件中的 PYTHON_EXE 后重试。
    pause
    exit /b 1
)

if not exist "%CD%\app.py" (
    echo 未在当前项目目录找到 app.py：%CD%
    pause
    exit /b 1
)

rem 旧服务可能是未更新的 app.py；不能共用 5010 端口。
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 5010 -State Listen -ErrorAction SilentlyContinue) { exit 10 }"
if errorlevel 10 (
    echo 5010 端口已被其他服务占用。
    echo 请在原服务窗口按 Ctrl+C 关闭旧服务，再双击本文件。
    pause
    exit /b 1
)

echo 正在从以下目录启动新版服务：
echo   %CD%
echo.
start "ETF分析 Web 服务（请保持此窗口打开）" /D "%CD%" "%PYTHON_EXE%" -X utf8 "%CD%\app.py"

echo 等待龙虎榜页面就绪...
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(45); do { try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 '%WEB_URL%'; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Milliseconds 500 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
    echo 服务未能在45秒内启动。请查看“ETF分析 Web 服务”窗口中的错误信息。
    pause
    exit /b 1
)

echo 服务已就绪，正在打开龙虎榜：
echo   %WEB_URL%
start "" "%WEB_URL%"
echo.
echo Web 服务正在另一个窗口运行；关闭那个窗口即可停止服务。
pause
