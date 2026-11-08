#!/bin/bash
# Double-click to start jobhunt on a Mac. Your browser opens http://127.0.0.1:8000 when it's ready.
# Close this window (or press Ctrl+C in it) to stop the site.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then echo "Run setup.command first."; read -p "Press Return to close."; exit 1; fi
.venv/bin/python run.py
