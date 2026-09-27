#!/usr/bin/env sh
# Atölye ERP - Linux/macOS starter. First run creates a virtualenv and installs dependencies.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python run.py "$@"
