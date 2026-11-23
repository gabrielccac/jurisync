#!/usr/bin/env bash
# Install or update the Jurisync hosted server on a fresh Ubuntu 24.04 VPS. Run as root; idempotent.
#
#   sudo bash install.sh --archive /tmp/jurisync.tar.gz --proxy quick                       # no domain yet
#   sudo bash install.sh --archive /tmp/jurisync.tar.gz --proxy caddy --domain mcp.example.com.br
#   sudo bash install.sh --archive /tmp/jurisync.tar.gz --proxy tunnel --domain mcp.example.com.br
#
# The archive is `git archive` of the repository (push.sh makes and copies it): tracked files only, so no customer
# .env, certificate or QR code ever reaches the server. Credentials arrive later as sealed bundles (provision apply).
set -euo pipefail

ARCHIVE="" PROXY=none DOMAIN="" UV_VERSION=0.11.8
while [ $# -gt 0 ]; do
  case "$1" in
    --archive) ARCHIVE=$2; shift 2 ;;
    --proxy) PROXY=$2; shift 2 ;;
    --domain) DOMAIN=$2; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 2; }
[ -f "$ARCHIVE" ] || { echo "--archive <jurisync.tar.gz> is required" >&2; exit 2; }
case "$PROXY" in
  caddy|tunnel) [ -n "$DOMAIN" ] || { echo "--proxy $PROXY needs --domain" >&2; exit 2; } ;;
  quick|none) ;;
  *) echo "--proxy must be caddy, tunnel, quick or none" >&2; exit 2 ;;
esac
. /etc/os-release
[ "${ID:-}" = ubuntu ] || echo "warning: tested on Ubuntu 24.04, this is ${PRETTY_NAME:-unknown}" >&2

APP=/opt/jurisync/app
STATE=/var/lib/jurisync
export DEBIAN_FRONTEND=noninteractive

echo "== packages"
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg rsync sqlite3 python3 xz-utils unzip \
  fonts-dejavu fonts-liberation fontconfig
apt-get install -y -q libreoffice-writer-nogui || apt-get install -y -q libreoffice-writer

echo "== Google Chrome (headless Eproc logins, e-SAJ bridge, AWS WAF CAPTCHA, HTML to PDF)"
if ! command -v google-chrome >/dev/null; then
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
    >/etc/apt/sources.list.d/google-chrome.list
  apt-get update -q
  apt-get install -y -q google-chrome-stable
fi

echo "== user and folders"
id jurisync >/dev/null 2>&1 || useradd --system --home-dir "$STATE" --create-home --shell /usr/sbin/nologin jurisync
install -d -m 0750 -o jurisync -g jurisync "$STATE" "$STATE/hosted" "$STATE/customers"
install -d -m 0755 /opt/jurisync
# The code and its virtualenv are root's, readable by the service's group: the service cannot change them.
install -d -m 0750 -o root -g jurisync "$APP"
install -d -m 0755 /etc/jurisync

echo "== code"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
tar -C "$WORK" -xzf "$ARCHIVE"
# Runtime state (ADVBOX session cookies, downloads) lives in $STATE (JURISYNC_STATE_DIR); an older install kept it in
# the code folder: move it once.
for dir in data downloaded_files; do
  if [ -d "$APP/$dir" ] && [ ! -e "$STATE/$dir" ]; then mv "$APP/$dir" "$STATE/$dir"; fi
done
install -d -m 0700 -o jurisync -g jurisync "$STATE/data"
chown -R jurisync:jurisync "$STATE/data" "$STATE/downloaded_files" 2>/dev/null || true
# Keep the virtualenv across updates.
rsync -a --delete --exclude .venv/ --exclude data/ --exclude downloaded_files/ --exclude conversions/ "$WORK"/ "$APP"/

echo "== uv $UV_VERSION and the virtualenv (with the captcha-ocr extra), as root"
UV=/opt/jurisync/bin/uv
if ! [ -x "$UV" ] || ! "$UV" --version | grep -q "$UV_VERSION"; then
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | env UV_INSTALL_DIR=/opt/jurisync/bin UV_NO_MODIFY_PATH=1 sh
fi
# A virtualenv an older install built as jurisync (its Python under $STATE) is rebuilt as root.
if [ -e "$APP/.venv/bin/python" ] && readlink -f "$APP/.venv/bin/python" | grep -q "^$STATE/"; then rm -rf "$APP/.venv"; fi
# A Python uv downloads goes under /opt/jurisync too: /root is hidden from the service (ProtectHome).
(cd "$APP" && env UV_PYTHON_INSTALL_DIR=/opt/jurisync/python UV_CACHE_DIR=/var/cache/jurisync-uv \
  "$UV" sync --frozen --no-dev --extra captcha-ocr)
chown -R root:jurisync "$APP"
chmod -R u=rwX,g=rX,o= "$APP"
[ -d /opt/jurisync/python ] && chmod -R go+rX /opt/jurisync/python

echo "== settings"
if [ ! -f /etc/jurisync/jurisync.env ]; then
  install -m 0640 -o root -g jurisync "$APP/deploy/jurisync.env.example" /etc/jurisync/jurisync.env
fi
if [ -n "$DOMAIN" ]; then
  sed -i "s|^JURISYNC_BASE_URL=.*|JURISYNC_BASE_URL=https://$DOMAIN|" /etc/jurisync/jurisync.env
  rm -f /etc/jurisync/base-url.env
fi
install -m 0755 "$APP/deploy/jurisync-admin" /usr/local/bin/jurisync-admin
install -m 0644 "$APP"/deploy/systemd/*.service "$APP"/deploy/systemd/*.timer /etc/systemd/system/

echo "== vault key (systemd credential)"
if ! ls /etc/credstore.encrypted/JURISYNC_VAULT_KEY /etc/credstore/JURISYNC_VAULT_KEY >/dev/null 2>&1; then
  "$APP/deploy/set-secret.sh" --generate JURISYNC_VAULT_KEY
fi
systemctl daemon-reload

echo "== store"
jurisync-admin init >/dev/null 2>&1 || true   # creates the data folder; the key it prints is discarded
install -d -m 0700 -o jurisync -g jurisync "$STATE/hosted"

echo "== reverse proxy: $PROXY"
case "$PROXY" in
  caddy)
    if ! command -v caddy >/dev/null; then
      curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /etc/apt/keyrings/caddy-stable.gpg
      echo "deb [signed-by=/etc/apt/keyrings/caddy-stable.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
        >/etc/apt/sources.list.d/caddy-stable.list
      apt-get update -q && apt-get install -y -q caddy
    fi
    sed "s/mcp.example.com.br/$DOMAIN/" "$APP/deploy/caddy/Caddyfile" >/etc/caddy/Caddyfile
    install -d -o caddy -g caddy /var/log/caddy
    systemctl enable caddy && systemctl reload-or-restart caddy
    systemctl disable --now jurisync-quicktunnel.service 2>/dev/null || true
    ;;
  tunnel|quick)
    if ! command -v cloudflared >/dev/null; then
      curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /etc/apt/keyrings/cloudflare-main.gpg
      echo "deb [signed-by=/etc/apt/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
        >/etc/apt/sources.list.d/cloudflared.list
      apt-get update -q && apt-get install -y -q cloudflared
    fi
    if [ "$PROXY" = quick ]; then
      systemctl enable jurisync-quicktunnel.service
    else
      systemctl disable --now jurisync-quicktunnel.service 2>/dev/null || true
      echo "Named tunnel: follow deploy/cloudflared/config.yml.example (cloudflared tunnel login/create/route dns)."
    fi
    ;;
esac

echo "== services"
systemctl enable jurisync-hosted.service jurisync-backup.timer
systemctl restart jurisync-hosted.service
systemctl start jurisync-backup.timer
[ "$PROXY" = quick ] && systemctl restart jurisync-quicktunnel.service
sleep 3
systemctl --no-pager --lines=0 status jurisync-hosted.service || true
echo
echo "Next: CAPTCHA key (sudo $APP/deploy/set-secret.sh JURISYNC_CAPTCHA_API_KEY), a copy of the vault key outside"
echo "the server (set-secret.sh --reveal), the offices (deploy/README.md, provision apply) and the smoke test."
