#!/usr/bin/env bash
# Store a server secret as a systemd credential for jurisync-hosted (and jurisync-admin). Run as root on the server.
#
#   sudo deploy/set-secret.sh --generate JURISYNC_VAULT_KEY     # once, at install: a new random vault key
#   sudo deploy/set-secret.sh JURISYNC_CAPTCHA_API_KEY          # asks for the value without echo (or reads stdin)
#   sudo deploy/set-secret.sh --reveal JURISYNC_VAULT_KEY       # print it once, to keep it in a password manager
#
# With systemd-creds (systemd >= 250, Ubuntu 24.04) the value is encrypted with the host key/TPM in
# /etc/credstore.encrypted/<NAME>; otherwise it is a root-only file in /etc/credstore/<NAME>. The service reads it from
# $CREDENTIALS_DIRECTORY: never from the environment, a command line or the unit file.
set -euo pipefail

ALLOWED="JURISYNC_VAULT_KEY JURISYNC_CAPTCHA_API_KEY JURISYNC_MONITOR_TOKEN"
MODE=set
case "${1:-}" in
  --generate) MODE=generate; shift ;;
  --reveal) MODE=reveal; shift ;;
  --force-generate) MODE=force-generate; shift ;;
esac
NAME=${1:?usage: set-secret.sh [--generate|--reveal] NAME}
[[ " $ALLOWED " == *" $NAME "* ]] || { echo "$NAME is not a server secret ($ALLOWED)" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 2; }

if systemd-creds --help 2>/dev/null | grep -q encrypt; then
  STORE=/etc/credstore.encrypted; DIRECTIVE=LoadCredentialEncrypted
else
  STORE=/etc/credstore; DIRECTIVE=LoadCredential
fi
install -d -m 0700 "$STORE"
TARGET="$STORE/$NAME"

if [ "$MODE" = reveal ]; then
  if [ "$DIRECTIVE" = LoadCredentialEncrypted ]; then systemd-creds decrypt --name="$NAME" "$TARGET" -; else cat "$TARGET"; fi
  exit 0
fi

if [ "$MODE" = generate ] && [ -e "$TARGET" ]; then
  echo "$TARGET already exists: a new vault key would make every stored vault unreadable. Nothing changed." >&2
  exit 1
fi

write() {  # value on stdin -> credential file
  if [ "$DIRECTIVE" = LoadCredentialEncrypted ]; then
    systemd-creds encrypt --name="$NAME" - "$TARGET.tmp"
  else
    (umask 077; cat >"$TARGET.tmp")
  fi
  chmod 0600 "$TARGET.tmp"
  mv "$TARGET.tmp" "$TARGET"
}

if [ "$MODE" = generate ] || [ "$MODE" = force-generate ]; then
  python3 -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())' | write
  echo "$NAME: nova chave gerada em $TARGET (não exibida)."
  echo "Guarde uma cópia fora do servidor: sudo $0 --reveal $NAME   (sem ela, o backup do cofre não abre)"
else
  if [ -t 0 ]; then
    read -r -s -p "Valor de $NAME: " VALUE; echo
    printf '%s' "$VALUE" | write
    unset VALUE
  else
    write
  fi
  echo "$NAME gravado em $TARGET."
fi

# The unit drop-in and jurisync-admin load every credential present.
install -d -m 0755 /etc/systemd/system/jurisync-hosted.service.d /etc/jurisync
{
  echo "[Service]"
  for file in "$STORE"/*; do
    [ -f "$file" ] || continue
    case "$file" in *.tmp) continue ;; esac
    echo "$DIRECTIVE=$(basename "$file"):$file"
  done
} >/etc/systemd/system/jurisync-hosted.service.d/credentials.conf
grep "^$DIRECTIVE=" /etc/systemd/system/jurisync-hosted.service.d/credentials.conf >/etc/jurisync/credential-properties
chmod 0644 /etc/jurisync/credential-properties
systemctl daemon-reload
