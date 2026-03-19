@echo off
REM ============================================================
REM 关闭所有实验组件
REM ============================================================
echo [清理] 关闭所有实验组件...
taskkill /fi "WINDOWTITLE eq MCP-Backend" /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq GW-NG" /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq GW-SRL" /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq GW-DP" /f >nul 2>&1
taskkill /im gateway.exe /f >nul 2>&1
echo [完成] 所有组件已关闭。
