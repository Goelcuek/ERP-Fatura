@echo off
REM Atölye ERP - Windows starter. Double-click to run.
REM First run creates a virtual environment and installs dependencies.
cd /d "%~dp0"
if not exist .venv (
  echo Kurulum yapiliyor / Installing...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python run.py %*
pause
