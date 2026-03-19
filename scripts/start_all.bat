@echo off
REM ============================================================
REM 一键启动完整实验环境
REM   Step 1: Python MCP 后端 (Core 4-15, Port 8080)
REM   Step 2: 三个 Go 网关 (Core 2-3, Port 9001/9002/9003)
REM
REM 用法: start_all.bat [sterile|battlefield]
REM ============================================================
if "%1"=="" (
    set MODE=sterile
) else (
    set MODE=%1
)

echo ================================================================
echo  MCP 服务治理实验 — 一键启动
echo  模式: %MODE%
echo ================================================================

echo.
echo [Step 1/4] 启动 Python MCP 后端 (Core 4-15, Port 8080)...
cd /d %~dp0\..\mcp_server
start "MCP-Backend" /affinity FFF0 python server.py --host 127.0.0.1 --port 8080 --mode %MODE% --cpu-affinity 4,5,6,7,8,9,10,11,12,13,14,15
echo   → 等待后端就绪 (3秒)...
timeout /t 3 /nobreak >nul

echo.
cd /d %~dp0\..
echo [编译] 构建 Go 网关...
go build -o gateway.exe ./cmd/gateway/
if errorlevel 1 (
    echo [错误] Go 编译失败!
    exit /b 1
)

echo.
echo [Step 2/4] 启动 NG 网关 (Core 2-3, Port 9001)...
start "GW-NG"  /affinity C gateway.exe --mode ng  --port 9001 --host 127.0.0.1 --backend http://127.0.0.1:8080
timeout /t 1 /nobreak >nul

echo [Step 3/4] 启动 SRL 网关 (Core 2-3, Port 9002)...
start "GW-SRL" /affinity C gateway.exe --mode srl --port 9002 --host 127.0.0.1 --backend http://127.0.0.1:8080
timeout /t 1 /nobreak >nul

echo [Step 4/4] 启动 DP 网关 (Core 2-3, Port 9003)...
start "GW-DP"  /affinity C gateway.exe --mode dp  --port 9003 --host 127.0.0.1 --backend http://127.0.0.1:8080
timeout /t 1 /nobreak >nul

echo.
echo ================================================================
echo  全部组件已启动:
echo    MCP Backend : http://127.0.0.1:8080  (Core 4-15)
echo    NG  Gateway : http://127.0.0.1:9001  (Core 2-3)
echo    SRL Gateway : http://127.0.0.1:9002  (Core 2-3)
echo    DP  Gateway : http://127.0.0.1:9003  (Core 2-3)
echo ================================================================
echo.
echo 按任意键关闭所有组件...
pause >nul

echo [清理] 关闭所有组件...
taskkill /fi "WINDOWTITLE eq MCP-Backend" /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq GW-NG" /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq GW-SRL" /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq GW-DP" /f >nul 2>&1
taskkill /im gateway.exe /f >nul 2>&1
echo [完成] 所有组件已关闭。
