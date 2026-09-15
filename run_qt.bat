@echo off
chcp 65001 >nul
title MC Panel - Qt 界面
cd /d "%~dp0"

set PY=C://Users//zhebushigudu//.workbuddy//binaries//python//envs//pyqt5//Scripts//python.exe

if not exist "%PY%" (
    echo.
    echo   [错误] 找不到装了 Qt 界面的 Python 环境：
    echo          %PY%
    echo.
    echo   请先执行下面三条命令把它装好（只需一次）：
    echo     python -m venv "%USERPROFILE%\.workbuddy\binaries\python\envs\pyqt5"
    echo     "%PY%" -m pip install PyQt5 numpy
    echo     "%PY%" -m pip install E://SRC//PyQt-SiliconUI
    echo.
    pause
    exit /b 1
)

echo   正在启动 Qt 界面（关掉窗口即退出）...
echo   提示：如果不指定任何参数，默认就会优先用 Qt 界面。
echo.
"%PY%" panel.py --qt %*
if errorlevel 1 (
    echo.
    echo   启动失败。把上面的报错发出来排查。
    echo   想用回原来的网页界面：双击 run.bat
    pause
)
