#!/usr/bin/env bash
# Daily backup (jurisync-backup.timer): the hosted store (online-safe SQLite copy), the encrypted vault files and the
# customer profiles, in one 0600 tarball. The vault key is NOT in it: keep the key's copy apart (set-secret.sh --reveal).
#
# /etc/jurisync/backup.env (optional): JURISYNC_BACKUP_DIR (default /var/backups/jurisync), JURISYNC_BACKUP_KEEP_DAYS
# (default 14), JURISYNC_BACKUP_REMOTE (an rclone remote:path; the tarball is copied there when set).
set -euo pipefail
umask 077

HOSTED=${JURISYNC_HOSTED_DIR:-/var/lib/jurisync/hosted}
PROFILES=${JURISYNC_CUSTOMERS_DIR:-/var/lib/jurisync/customers}
DEST=${JURISYNC_BACKUP_DIR:-/var/backups/jurisync}
KEEP=${JURISYNC_BACKUP_KEEP_DAYS:-14}
STAMP=$(date +%Y%m%d-%H%M%S)

install -d -m 0700 "$DEST"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/jurisync-$STAMP"
# .backup reads through SQLite (the WAL included) while the server writes; a plain cp of store.sqlite would miss the WAL.
sqlite3 -cmd ".timeout 5000" "$HOSTED/store.sqlite" ".backup '$WORK/jurisync-$STAMP/store.sqlite'"
sqlite3 "$WORK/jurisync-$STAMP/store.sqlite" "pragma integrity_check" | grep -qx ok
[ -d "$HOSTED/vault" ] && cp -a "$HOSTED/vault" "$WORK/jurisync-$STAMP/vault"
[ -d "$PROFILES" ] && cp -a "$PROFILES" "$WORK/jurisync-$STAMP/customers"
tar -C "$WORK" -czf "$DEST/jurisync-$STAMP.tar.gz" "jurisync-$STAMP"
chmod 0600 "$DEST/jurisync-$STAMP.tar.gz"
find "$DEST" -name 'jurisync-*.tar.gz' -mtime +"$KEEP" -delete
if [ -n "${JURISYNC_BACKUP_REMOTE:-}" ]; then
  rclone copy "$DEST/jurisync-$STAMP.tar.gz" "$JURISYNC_BACKUP_REMOTE"
fi
echo "backup: $DEST/jurisync-$STAMP.tar.gz ($(ls "$HOSTED/vault" 2>/dev/null | wc -l) cofre(s))"
