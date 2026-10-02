#!/bin/sh
# Start tailscaled (bez roota, userspace) — Funnel wystawia hub pod stałym adresem https://<host>.<tailnet>.ts.net, a panel (port 8766) tylko w tailnecie pod :8443.
# Stan logowania i konfiguracja Funnela leżą w /home/openclaw/opt/ts/state. Uruchamiany z crontaba (@reboot).
TS=/home/openclaw/opt/ts
cd "$TS"
if ! ps -eo args | grep -q "^$TS/tailscaled\|^./tailscaled"; then
  setsid nohup "$TS/tailscaled" --tun=userspace-networking --statedir="$TS/state" --socket="$TS/tailscaled.sock" >> "$TS/tailscaled.log" 2>&1 < /dev/null &
  sleep 5
fi
timeout 20 "$TS/tailscale" --socket="$TS/tailscaled.sock" up --hostname=skywatch --timeout=15s
timeout 20 "$TS/tailscale" --socket="$TS/tailscaled.sock" funnel --bg 8765
timeout 20 "$TS/tailscale" --socket="$TS/tailscaled.sock" serve --bg --https=8443 http://127.0.0.1:8766   # panel: tylko tailnet
