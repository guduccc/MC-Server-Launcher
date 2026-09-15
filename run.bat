@echo off
chcp 65001 >nul
title MC Panel - Minecraft 面板开服器
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [错误] 没有找到 python 命令。
    echo   请先安装 Python 3.8 或更高版本：https://www.python.org/downloads/
    echo   安装时记得勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)

echo.
echo   正在启动 MC Panel ...
echo   浏览器会自动打开面板，关闭这个窗口即退出面板。
echo   如果面板里还有服务端在运行，加一个参数可以退出时一起关掉：
echo       run.bat --stop-all-on-exit
echo.

python "%~dp0panel.py" %*

echo.
echo   面板已退出。
pause
