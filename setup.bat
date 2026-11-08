@echo off
rem jobhunt setup for Windows. Double-click it. Creates .venv, installs the requirements, makes your .env from
rem .env.example and an empty database. Safe to run again.
cd /d "%~dp0"
set PY=
where py >nul 2>nul && (py -3.12 -c "" >nul 2>nul && set PY=py -3.12)
if not defined PY where py >nul 2>nul && (py -3.11 -c "" >nul 2>nul && set PY=py -3.11)
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set PY=python
if not defined PY (
  echo Python 3.11 or newer is needed. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this again.
  pause
  exit /b 1
)
echo Using %PY%
if not exist .venv %PY% -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
if errorlevel 1 ( echo Installing the requirements failed. & pause & exit /b 1 )
if not exist .env ( copy .env.example .env >nul & echo Created .env ^(add your own keys there; see Settings ^> Keys ^& usage^). )
if not exist data mkdir data
".venv\Scripts\python.exe" -c "from storage.db import init_db; init_db()"
where claude >nul 2>nul || if not exist "%USERPROFILE%\.local\bin\claude.exe" (
  echo.
  echo Claude Code isn't installed yet. The site works without it; AI drafts need it. Install and sign in with YOUR account:
  echo   PowerShell: irm https://claude.ai/install.ps1 ^| iex     then run: claude   ^(and log in^)
)
echo.
echo Setup done. Double-click "Start jobhunt.bat" to open the site.
pause
