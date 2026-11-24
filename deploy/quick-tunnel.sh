#!/usr/bin/env bash
# Temporary public address before the domain exists: a Cloudflare quick tunnel (https://<random>.trycloudflare.com).
# Started by jurisync-quicktunnel.service. The address changes on every start, so it is written as
# JURISYNC_BASE_URL (the OAuth issuer must be the public address) and the server is restarted with it.
set -euo pipefail

LOG=$(mktemp)
trap 'rm -f "$LOG"; kill "${PID:-0}" 2>/dev/null || true' EXIT
PORT=$(sed -n 's/^JURISYNC_PORT=//p' /etc/jurisync/jurisync.env)
runuser -u jurisync -- cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:${PORT:-8080}" >"$LOG" 2>&1 &
PID=$!

URL=""
for _ in $(seq 1 60); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -n1 || true)
  [ -n "$URL" ] && break
  kill -0 "$PID" 2>/dev/null || { cat "$LOG" >&2; exit 1; }
  sleep 1
done
[ -n "$URL" ] || { echo "cloudflared did not report an address" >&2; exit 1; }

umask 022
printf 'JURISYNC_BASE_URL=%s\n' "$URL" >/etc/jurisync/base-url.env
systemctl restart jurisync-hosted.service
echo "Endereço temporário: ${URL}/tribunais/mcp (e ${URL}/advbox/mcp, ${URL}/juridiq/mcp; o conector do Claude/ChatGPT usa estes endereços até o domínio existir)"
wait "$PID"
