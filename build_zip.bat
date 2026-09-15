@echo off
chcp 65001 >nul
title MC Panel - 打包源码
cd /d "%~dp0"

echo.
echo ============================================================
echo   MC Panel 打包源码 zip
echo ============================================================
echo.

REM 源码打包只用标准库 zipfile，不需要虚拟环境、不需要装任何东西
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

%PY% -c "import sys; print('  使用 Python', sys.version.split()[0])"
echo   开始打包（含校验，约 10 秒）...
echo.
%PY% build_zip.py %*
set CODE=%errorlevel%
echo.
if not "%CODE%"=="0" (
    echo ============================================================
    echo   打包或校验失败，退出码 %CODE%
    echo   请把上面的报错信息发出来排查。
    echo ============================================================
    pause
    exit /b %CODE%
)

echo   产物已放到上一级目录：%~dp0..\
explorer "%~dp0.."
pause
