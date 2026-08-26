@echo off
setlocal EnableExtensions
title TruckingWithDoc FunFact Bot

rem Run from this folder so config.json / tokens.json are found no matter
rem where the .bat is launched from (double-click, shortcut, etc.).
cd /d "%~dp0"

rem ---- locate Python (plain "python" or the "py" launcher) -------------
set "PYCMD="
where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
    echo.
    echo  [ERROR] Python 3 was not found on PATH.
    echo          Install it from https://www.python.org/downloads/
    echo          and tick "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

rem Show UTF-8 output properly in the console window.
chcp 65001 >nul 2>nul

echo.
echo  ==============================================================
echo   TruckingWithDoc FunFact Bot
echo   Python : %PYCMD%
echo   Args   : %*
echo   Ctrl+C stops the bot. It restarts automatically on crashes.
echo  ==============================================================
echo.

:loop
%PYCMD% bot.py %*
set "EXITCODE=%ERRORLEVEL%"

if "%EXITCODE%"=="0" (
    echo.
    echo  [OK] Bot exited cleanly.
    goto :stopped
)

echo.
echo  [WARN] Bot exited with code %EXITCODE%.
choice /c YN /t 5 /d Y /m "  Restart in 5 seconds? (Y=yes, N=no)"
if errorlevel 2 goto :stopped
echo  [INFO] Restarting...
echo.
goto :loop

:stopped
echo.
echo  Bot stopped.
pause
endlocal
