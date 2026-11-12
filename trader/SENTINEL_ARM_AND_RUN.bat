@echo off
REM ===========================================================================
REM  SENTINEL_ARM_AND_RUN.bat -- arm the Sentinel-Witness trader FOR A LIMITED
REM  TIME and run ONE live cycle. 2026-10-05; rewritten 2026-11-12 (H7).
REM
REM  THIS CAN PLACE REAL ORDERS. It sets "armed": true in trader_config.json
REM  together with "armed_until" (now + ARM_HOURS, UTC), records who and when,
REM  and runs:
REM      "%PY%" covenant_trader.py --once     (appended to trader_log.txt)
REM
REM  WHAT CHANGED 2026-11-12, AND WHY. Until then this file armed on one key
REM  press (choice Y/N) and the arming never ended: "armed": true stayed in the
REM  config until somebody edited it back. Now
REM    - it SHOWS what arming means and the trader's own status first;
REM    - it arms only if the operator TYPES the phrase below, exactly;
REM    - the arming EXPIRES: guards.arming() reads "armed": true as armed only
REM      with an unexpired armed_until at most 24 hours ahead, so the trader
REM      disarms itself at that time whether or not anybody remembers;
REM    - the Python that runs is this repository's .venv, else the PINNED
REM      covenant folder's (the one line of
REM      %%USERPROFILE%%\.sentinel_witness\covenant_home.pin), else `python`.
REM      It is no longer taken from covenant_home in trader_config.json: that
REM      let the config being armed choose the interpreter that arms it.
REM
REM  What still has to say yes, every order, every time:
REM    - $25 per order, $50 per day, 2 orders per day (hard ceilings in
REM      guards.py; trader_config.json may only lower them)
REM    - the guard stack on buys, including the 10%% cash floor
REM    - the frozen hold-only floors on XRP, LINK and HBAR (covenant's
REM      private\RESERVE.json, read in place)
REM    - TODAY's plan approved in covenant (on the phone, or covenant_daily_plan.py
REM      --approve run in covenant's folder)
REM    - the decision sealed by covenant's node
REM    - Rule 5. An ARMED config that carries a rule5_waiver record, a
REM      trading_scope reserve under 50%%, or a witness folder that is not the
REM      pinned one is REFUSED for the whole cycle (H6, 2026-11-12).
REM
REM  To stop: drop a file named TRADER_HALT in this folder OR in covenant's,
REM  or set "armed": false in trader_config.json.
REM ===========================================================================
setlocal
cd /d "%~dp0"
set "ARM_HOURS=12"
set "ARM_PHRASE=ARM REAL ORDERS"
set "PY="
set "COVHOME="
if exist "..\.venv\Scripts\python.exe" set "PY=..\.venv\Scripts\python.exe"
set "PINFILE=%USERPROFILE%\.sentinel_witness\covenant_home.pin"
if not defined PY if exist "%PINFILE%" for /f "usebackq eol=# delims=" %%H in ("%PINFILE%") do if not defined COVHOME set "COVHOME=%%H"
if not defined PY if defined COVHOME if exist "%COVHOME%\.venv\Scripts\python.exe" set "PY=%COVHOME%\.venv\Scripts\python.exe"
if not defined PY set "PY=python"

if not exist "trader_config.json" goto :noconfig

echo.
echo  ==========================================================================
echo   ARMING THE SENTINEL-WITNESS TRADER
echo  ==========================================================================
echo.
echo   WHAT ARMING MEANS
echo    - This program will place REAL orders, with REAL money, at your
echo      exchange, by itself. No further confirmation is asked per order.
echo    - It runs ONE live cycle as soon as you confirm below.
echo    - It then STAYS ARMED for %ARM_HOURS% hours. Any cycle that runs in that
echo      time, including the scheduled task, can place real orders too.
echo    - After %ARM_HOURS% hours it disarms itself. An arming never lasts longer
echo      than 24 hours, whatever trader_config.json says.
echo    - An order that has been placed cannot be taken back by this program.
echo.
echo   WHAT STILL HAS TO SAY YES TO EVERY ORDER
echo    - at most $25 an order, $50 a day, 2 orders a day
echo    - today's plan approved in covenant, and the order must be IN that plan
echo    - the decision sealed by covenant's node
echo    - Rule 5, the reserve floors, and the guard stack on buys
echo.
echo   THE TRADER'S OWN STATUS RIGHT NOW  (read it; a line that says REFUSED
echo   or REHEARSAL ONLY means an armed cycle will place nothing)
echo  --------------------------------------------------------------------------
"%PY%" covenant_trader.py --status
echo  --------------------------------------------------------------------------
echo.
echo   To arm for %ARM_HOURS% hours and run one live cycle now, type this phrase
echo   exactly, capitals included, and press Enter:
echo.
echo        %ARM_PHRASE%
echo.
echo   Anything else, or Enter alone, stops here with nothing changed.
echo.
set "TYPED="
set /p "TYPED=  type the phrase: "
"%PY%" -c "import os,sys; p=os.environ.get('ARM_PHRASE',''); sys.exit(0 if p and os.environ.get('TYPED','')==p else 1)"
if errorlevel 1 goto :notarmed

"%PY%" -c "import json,os,time;p='trader_config.json';c=json.load(open(p,encoding='utf-8'));h=float(os.environ['ARM_HOURS']);assert isinstance(c,dict) and 0<h<=24;n=time.time();f=lambda t:time.strftime('%%Y-%%m-%%dT%%H:%%M:%%SZ',time.gmtime(t));c['armed']=True;c['armed_by']='SENTINEL_ARM_AND_RUN.bat, phrase typed by the operator';c['armed_at']=f(n);c['armed_until']=f(n+h*3600);t=p+'.arming.tmp';w=open(t,'w',encoding='utf-8');json.dump(c,w,indent=2);w.flush();os.fsync(w.fileno());w.close();os.replace(t,p);print('  armed: true, until '+c['armed_until']+' UTC  (your typed phrase, recorded in trader_config.json)')"
if errorlevel 1 goto :armfailed

echo. >> trader_log.txt
echo ==== %DATE% %TIME% SENTINEL_ARM_AND_RUN ==== >> trader_log.txt
"%PY%" covenant_trader.py --once >> trader_log.txt 2>&1
if errorlevel 1 goto :cyclefailed
echo.
echo  Cycle finished. Its full report is the end of trader_log.txt.
powershell -NoProfile -Command "Get-Content trader_log.txt -Tail 45"
echo.
echo  The trader stays armed until the time printed above, then disarms itself.
echo  To disarm sooner: set "armed": false in trader_config.json, or drop a
echo  file named TRADER_HALT in this folder.
echo.
pause
exit /b 0

:notarmed
echo.
echo  Not armed. The phrase was not typed exactly. Nothing was changed.
pause
exit /b 0

:cyclefailed
REM  G13 (review round 1, 2026-11-12). The config was armed above, BEFORE the
REM  first cycle showed it was fit to run armed. A cycle that refused (a missing
REM  witness pin, a waiver, a cap over its ceiling, an unreadable state) used to
REM  leave "armed": true in place for the whole arming period, so a later
REM  scheduled run could go live after the operator believed arming had failed.
"%PY%" -c "import json,os;p='trader_config.json';c=json.load(open(p,encoding='utf-8'));assert isinstance(c,dict);c['armed']=False;c.pop('armed_until',None);t=p+'.disarm.tmp';f=open(t,'w',encoding='utf-8');json.dump(c,f,indent=1);f.flush();os.fsync(f.fileno());f.close();os.replace(t,p);print('  trader_config.json: armed set back to false')"
if errorlevel 1 goto :disarmfailed
goto :cyclereport

:disarmfailed
REM  Second review pass: a disarm that fails must not end in a warning alone.
REM  A halt file stops every cycle whatever the config says.
echo halted by SENTINEL_ARM_AND_RUN: the disarm after a failed first cycle did not complete> TRADER_HALT
echo  COULD NOT DISARM. A file named TRADER_HALT has been written in this folder,
echo  which stops every cycle. Set "armed": false in trader_config.json by hand,
echo  then delete TRADER_HALT.
pause
exit /b 1

:cyclereport
echo.
echo  The first armed cycle did not complete, so the trader has been DISARMED again.
echo  Why it stopped is at the end of trader_log.txt:
powershell -NoProfile -Command "Get-Content trader_log.txt -Tail 25"
pause
exit /b 1

:armfailed
echo.
echo  Could not arm -- trader_config.json was not changed. Nothing ran.
pause
exit /b 1

:noconfig
echo.
echo  There is no trader_config.json in this folder, so there is nothing to
echo  arm. Nothing was changed. See README.md, Setup.
pause
exit /b 1
