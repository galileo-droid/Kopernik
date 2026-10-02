#!/bin/sh
# Start huba + tymczasowego tunelu Cloudflare (trycloudflare) — do testów, zanim będzie stała domena.
# Uruchamiany z crontaba (@reboot). Nowy adres tunelu trafia do dist/public.json (format pliku bootstrap)
# i (z --notify) na Telegram do SKYWATCH_NOTIFY_TG z hub.env. Przejściowe: od 0.10 stały adres przez Tailscale Funnel.
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
if ! pgrep -f "python3 hub.py$" >/dev/null; then
  (set -a; . ./hub.env; set +a; setsid nohup python3 hub.py >> hub.log 2>&1 < /dev/null &)
fi
pkill -x cloudflared; sleep 1
LOG=/home/openclaw/opt/cloudflared.log
setsid nohup /home/openclaw/opt/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8765 > "$LOG" 2>&1 < /dev/null &
for i in $(seq 1 60); do
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" | head -1); [ -n "$URL" ] && break; sleep 1
done
[ -z "$URL" ] && { echo "brak adresu tunelu"; exit 1; }
printf '{"url": "%s"}\n' "$URL" > dist/public.json
echo "$URL"
NOTIFY=$(sed -n "s/^SKYWATCH_NOTIFY_TG=//p" hub.env)
[ "$1" = "--notify" ] && [ -n "$NOTIFY" ] && openclaw message send --channel telegram -t "$NOTIFY" \
  -m "SkywatchPL: tymczasowy tunel wstał pod nowym adresem $URL" >/dev/null 2>&1
exit 0
