@echo off
REM ===========================================================================
REM  SENTINEL_ARM_AND_RUN.bat -- arm the Sentinel-Witness trader and run ONE
REM  live cycle. 2026-10-05.
REM
REM  THIS CAN PLACE REAL ORDERS. It sets "armed": true in trader_config.json,
REM  records who and when, and runs:
REM      "%PY%" covenant_trader.py --once     (appended to trader_log.txt)
REM
REM  What still has to say yes, every order, every time:
REM    - $25 per order, $50 per day, 2 orders per day (trader_config.json)
REM    - the guard stack on buys, including the 10%% cash floor
REM    - the frozen hold-only floors on XRP, LINK and HBAR (covenant's
REM      private\RESERVE.json, read in place)
REM    - TODAY's plan approved in covenant (on the phone, or covenant_daily_plan.py
REM      --approve run in covenant's folder)
REM    - the decision sealed by covenant's node
REM  Rule 5 is waived only if trader_config.json carries a complete
REM  rule5_waiver record; otherwise it refuses as before.
REM
REM  To stop: drop a file named TRADER_HALT in this folder OR in covenant's,
REM  or set "armed": false in trader_config.json.
REM ===========================================================================
setlocal
cd /d "%~dp0"
set "PY="
if exist "..\.venv\Scripts\python.exe" set "PY=..\.venv\Scripts\python.exe"
if not defined PY (
  for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "try { (Get-Content -Raw 'trader_config.json' | ConvertFrom-Json).covenant_home } catch {}"`) do set "COVHOME=%%H"
)
if not defined PY if defined COVHOME if exist "%COVHOME%\.venv\Scripts\python.exe" set "PY=%COVHOME%\.venv\Scripts\python.exe"
if not defined PY set "PY=python"

echo.
echo  This ARMS the Sentinel-Witness trader and runs one live cycle.
echo  Orders that pass every check above are REAL.
choice /C YN /M "Arm and run now"
if errorlevel 2 (
  echo  Not armed. Nothing was changed.
  pause
  exit /b 0
)
"%PY%" -c "import json,time;p='trader_config.json';c=json.load(open(p));c['armed']=True;c['armed_by']='SENTINEL_ARM_AND_RUN.bat';c['armed_at']=time.strftime('%%Y-%%m-%%dT%%H:%%M:%%SZ',time.gmtime());json.dump(c,open(p,'w'),indent=2);print('  armed: true  (your click, recorded in trader_config.json)')"
if errorlevel 1 (
  echo  Could not arm -- trader_config.json was not changed. Nothing ran.
  pause
  exit /b 1
)
echo. >> trader_log.txt
echo ==== %DATE% %TIME% SENTINEL_ARM_AND_RUN ==== >> trader_log.txt
"%PY%" covenant_trader.py --once >> trader_log.txt 2>&1
echo.
echo  Cycle finished. Its full report is the end of trader_log.txt.
powershell -NoProfile -Command "Get-Content trader_log.txt -Tail 45"
echo.
pause
