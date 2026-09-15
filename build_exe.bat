@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title MC Panel - 打包 EXE
cd /d "%~dp0"

echo.
echo ============================================================
echo   MC Panel 打包 EXE
echo ============================================================
echo.

REM ---------- 1. 找一个可用的 Python ----------
set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" (
    where python >nul 2>nul && set PY=python
)
if "%PY%"=="" (
    echo   [错误] 没有找到 Python。
    echo   请先安装 Python 3.8 或更高版本：https://www.python.org/downloads/
    echo   安装时记得勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)
echo   [1/6] 使用 Python：%PY%
%PY% -c "import sys; print('        版本', sys.version.split()[0])"

REM ---------- 2. 准备独立的打包环境 ----------
set VENV=%~dp0.buildenv
set VPY=%VENV%\Scripts\python.exe

REM 目录被挪走 / 拷到别的电脑后，venv 里的绝对路径会失效，所以这里要真跑一下验证
set NEED=1
if exist "%VPY%" (
    "%VPY%" -c "import sys" >nul 2>nul
    if not errorlevel 1 set NEED=0
)
if "%NEED%"=="1" (
    echo   [2/6] 准备独立打包环境 .buildenv（首次约需 30 秒）...
    %PY% -m venv "%VENV%"
    if errorlevel 1 (
        echo         [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
    "%VPY%" -m pip install --upgrade pip -q
) else (
    echo   [2/6] 复用已有环境 .buildenv
)

REM ---------- 3. 安装打包依赖 ----------
"%VPY%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo   [3/6] 安装 PyInstaller ...
    "%VPY%" -m pip install pyinstaller -q
    if errorlevel 1 (
        echo         [错误] PyInstaller 安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
) else (
    echo   [3/6] PyInstaller 已安装
)

REM pywebview 决定能不能打「桌面窗口版」
set HASWV=1
"%VPY%" -c "import webview" >nul 2>nul
if errorlevel 1 set HASWV=0
if "%HASWV%"=="1" (
    echo   [4/6] pywebview 已安装，将打包「桌面窗口版 + 控制台版」
) else (
    echo   [4/6] 未安装 pywebview，只打包「控制台版」。
    echo         想要原生窗口版请执行：
    echo         "%VPY%" -m pip install pywebview
)

REM ---------- 5. 生成图标（缺失时才做） ----------
if not exist "app.ico" (
    echo   [5/6] 生成图标 app.ico ...
    "%VPY%" make_icon.py
) else (
    echo   [5/6] 图标已存在
)

REM ---------- 6. 打包 + 自动验证 ----------
echo   [6/6] 开始打包（首次 2~4 分钟，请勿关闭窗口）...
echo.
if "%HASWV%"=="1" (
    "%VPY%" build_exe.py %*
) else (
    "%VPY%" build_exe.py --mode console %*
)
set CODE=%errorlevel%
echo.
if not "%CODE%"=="0" (
    echo ============================================================
    echo   打包失败，退出码 %CODE%
    echo   请把上面的报错信息发出来排查。
    echo ============================================================
    pause
    exit /b %CODE%
)

echo ============================================================
echo   打包完成，产物在 dist\ 目录：
echo.
echo     mc-panel.exe          桌面窗口版 —— 双击就是一个原生窗口应用
echo     mc-panel-console.exe  控制台版   —— 终端 + 浏览器界面
echo.
echo   这两个 exe 可以单独拷走，目标电脑不用装 Python。
echo   注意：别放到 C://Program Files，那里没有写权限。
echo   数据（panel.json、servers）会生成在 exe 旁边的目录里。
echo ============================================================
echo.
explorer "%~dp0dist"
pause
