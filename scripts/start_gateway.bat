@echo off
REM ============================================================
REM 启动 Go 网关 — 绑定 Core 2,3
REM CPU Affinity Mask: Core 2,3 = 0x000C
REM
REM 用法: start_gateway.bat [ng|srl|dp]
REM ============================================================
if "%1"=="" (
    echo 用法: start_gateway.bat [ng^|srl^|dp]
    echo   ng  - No Governance 基线 (Port 9001)
    echo   srl - Static Rate Limit 基线 (Port 9002)
    echo   dp  - Dynamic Pricing 治理 (Port 9003)
    exit /b 1
)

set MODE=%1

if "%MODE%"=="ng" (
    set PORT=9001
    set TITLE=GW-NG
) else if "%MODE%"=="srl" (
    set PORT=9002
    set TITLE=GW-SRL
) else if "%MODE%"=="dp" (
    set PORT=9003
    set TITLE=GW-DP
) else (
    echo 未知模式: %MODE%
    exit /b 1
)

echo [Gateway] 启动 %MODE% 网关 (Core 2-3, Port %PORT%)...

cd /d %~dp0\..
REM 先编译
go build -o gateway.exe ./cmd/gateway/
if errorlevel 1 (
    echo [Gateway] 编译失败!
    exit /b 1
)

REM 使用 start /affinity 绑核启动
start "%TITLE%" /affinity C gateway.exe --mode %MODE% --port %PORT% --host 127.0.0.1 --backend http://127.0.0.1:8080
echo [Gateway] %MODE% 网关已启动 (PID 见窗口标题 %TITLE%)
