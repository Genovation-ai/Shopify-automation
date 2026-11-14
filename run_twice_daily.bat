@echo off
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
set PYTHONIOENCODING=utf-8

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm"') do set TS=%%i
set LOGFILE=logs\run_%TS%.log

echo === Starting Tech Genius Price Agent at %DATE% %TIME% === >> "%LOGFILE%"
"C:\Users\Harsh\AppData\Local\Python\pythoncore-3.14-64\python.exe" -u scheduled_run.py --approve-all --apply >> "%LOGFILE%" 2>&1
echo === Finished at %DATE% %TIME% === >> "%LOGFILE%"