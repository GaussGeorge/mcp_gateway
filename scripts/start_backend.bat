@echo off
REM ============================================================
REM 启动 MCP Python 后端 — 绑定 Core 4~15
REM CPU Affinity Mask: Core 4-15 = 0xFFF0
REM ============================================================
echo [MCP Backend] 启动 Python MCP 后端 (Core 4-15, Port 8080)...
echo [MCP Backend] 模式: %1
if "%1"=="" (
    set MODE=sterile
) else (
    set MODE=%1
)

cd /d %~dp0\..\mcp_server
start "MCP-Backend" /affinity FFF0 python server.py --host 127.0.0.1 --port 8080 --mode %MODE% --cpu-affinity 4,5,6,7,8,9,10,11,12,13,14,15
echo [MCP Backend] 已启动 (PID 见窗口标题 MCP-Backend)
timeout /t 3 /nobreak >nul
