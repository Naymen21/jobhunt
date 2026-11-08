#!/bin/bash
# Adds the two daily autopilot runs to launchd (macOS): 6:30 AM morning run, 1:30 PM catch-up.
# Usage:  ./scripts/schedule_mac.sh            install (or update)
#         ./scripts/schedule_mac.sh --remove   remove both
set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$ROOT/data"

write() {  # label hour minute extra-args
  local plist="$AGENTS/$1.plist"
  launchctl unload "$plist" 2>/dev/null || true
  if [ "$REMOVE" = "1" ]; then rm -f "$plist"; echo "removed $plist"; return; fi
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$1</string>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>scripts/autopilot.py</string>$4</array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>$2</integer><key>Minute</key><integer>$3</integer></dict>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>$ROOT/data/autopilot-launchd.log</string>
  <key>StandardErrorPath</key><string>$ROOT/data/autopilot-launchd.log</string>
</dict></plist>
EOF
  launchctl load "$plist"
  echo "wrote $plist"
}

REMOVE=0
[ "$1" = "--remove" ] && REMOVE=1
if [ "$REMOVE" = "0" ] && [ ! -x "$PY" ]; then echo "Run ./setup.command first (no .venv yet)."; exit 1; fi
write com.jobhunt.autopilot 6 30 ""
write com.jobhunt.catchup 13 30 "<string>--kind</string><string>catch-up</string>"
[ "$REMOVE" = "0" ] && echo "Done. Test it now: launchctl start com.jobhunt.autopilot"
