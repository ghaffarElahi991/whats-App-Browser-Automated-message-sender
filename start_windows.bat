@echo off
setlocal
cd /d "%~dp0"
py -3 run.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo The application could not start. Review the error above.
pause
exit /b 1
