#!/usr/bin/env bash
# From our machine: copy the committed code to the VPS and run install.sh there.
#
#   deploy/push.sh ubuntu@203.0.113.10 --proxy quick
#   deploy/push.sh ubuntu@203.0.113.10 --proxy caddy --domain mcp.example.com.br
#
# Only `git archive HEAD` travels: tracked files, never customers/*/.env, certificates, QR codes or the gitignored
# customer folders. Commit before pushing; the archive is the commit, not the working tree.
set -euo pipefail
HOST=${1:?usage: push.sh user@host [install.sh options]}
shift
ROOT=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
ARCHIVE=$(mktemp --suffix=.tar.gz)
trap 'rm -f "$ARCHIVE"' EXIT
git -C "$ROOT" archive --format=tar.gz -o "$ARCHIVE" HEAD
echo "commit $(git -C "$ROOT" rev-parse --short HEAD) -> $HOST"
scp -q "$ARCHIVE" "$HOST:/tmp/jurisync.tar.gz"
ssh -t "$HOST" "tar -xzf /tmp/jurisync.tar.gz -O deploy/install.sh > /tmp/jurisync-install.sh && sudo bash /tmp/jurisync-install.sh --archive /tmp/jurisync.tar.gz $* && rm -f /tmp/jurisync.tar.gz /tmp/jurisync-install.sh"
