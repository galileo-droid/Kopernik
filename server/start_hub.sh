#!/bin/sh
# Start huba (bez tunelu). Uruchamiany z crontaba (@reboot); publiczny dostęp daje Tailscale Funnel (start_tailscale.sh).
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
if ! pgrep -f "python3 hub.py$" >/dev/null; then
  (set -a; . ./hub.env; set +a; setsid nohup python3 hub.py >> hub.log 2>&1 < /dev/null &)
fi
exit 0
