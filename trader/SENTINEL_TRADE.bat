@echo off
REM ===========================================================================
REM  SENTINEL_TRADE.bat -- the Sentinel-Witness trader, read-only. 2026-10-05.
REM
REM    1. "%PY%" money_posture.py              ARMED or DISARMED, said plainly
REM    2. "%PY%" covenant_trader.py --status   caps, Rule 5 waiver, covenant, floors
REM    3. "%PY%" covenant_trader.py --plan-only
REM         today's plan. It reads balances, plans, and seals the decision
REM         through covenant's node; it calls no venue's order endpoint.
REM
REM  Nothing here arms the trader or places an order. That is
REM  SENTINEL_ARM_AND_RUN.bat, and it asks first.
REM
REM  PYTHON: this repository's .venv if it has one, else covenant's .venv (read
REM  from covenant_home in trader_config.json), else whatever `python` is.
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
"%PY%" money_posture.py
echo.
"%PY%" covenant_trader.py --status
echo.
"%PY%" covenant_trader.py --plan-only
echo.
pause
