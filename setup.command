#!/bin/bash
# jobhunt setup for macOS (and Linux). Double-click it in Finder, or run ./setup.command in Terminal.
# Creates .venv, installs the requirements, makes your .env from .env.example and an empty database. Safe to run again.
cd "$(dirname "$0")"
set -e
PY=""
for c in python3.12 python3.11 python3; do
  if command -v $c >/dev/null 2>&1 && $c -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then PY=$c; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 or newer is needed. Install it from https://www.python.org/downloads/ (or: brew install python@3.12), then run this again."
  read -p "Press Return to close."; exit 1
fi
echo "Using $($PY --version)"
[ -d .venv ] || $PY -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q
[ -f .env ] || { cp .env.example .env; echo "Created .env (add your own keys there; see Settings > Keys & usage)."; }
mkdir -p data
.venv/bin/python -c "from storage.db import init_db; init_db()"
chmod +x "Start jobhunt.command" scripts/*.sh 2>/dev/null || true
if ! command -v claude >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/claude" ]; then
  echo ""
  echo "Claude Code isn't installed yet. The site works without it; AI drafts need it. Install and sign in with YOUR account:"
  echo "  curl -fsSL https://claude.ai/install.sh | bash   then run: claude   (and log in)"
fi
echo ""
echo "Setup done. Double-click 'Start jobhunt.command' to open the site."
read -p "Press Return to close."
