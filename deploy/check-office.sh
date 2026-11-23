#!/usr/bin/env bash
# First login of each portal of one office, on the server, one at a time (docs/remote-mcp-migration-plan.md).
# Stops at the first failure: several portals share one account (PDPJ SSO, an Eproc family), and a wrong password
# sent again to the next portal counts as another failed attempt on the same account.
#
#   sudo deploy/check-office.sh victor [/var/lib/jurisync/rollout/plan.json] [pause seconds, default 30]
set -euo pipefail
OFFICE=${1:?usage: check-office.sh <office> [plan.json] [pause]}
PLAN=${2:-/var/lib/jurisync/rollout/plan.json}
PAUSE=${3:-30}
mapfile -t CHECKS < <(python3 -c '
import json, sys
plan = json.load(open(sys.argv[1]))
office = next((o for o in plan["offices"] if o["tenant"] == sys.argv[2]), None)
if office is None:
    sys.exit(f"{sys.argv[2]} is not in the plan")
print("\n".join(office["checks"]))
' "$PLAN" "$OFFICE")
echo "$OFFICE: ${#CHECKS[@]} login(s), um por vez, ${PAUSE}s entre eles: ${CHECKS[*]}"
read -r -p "Confirmar (s/N)? " ANSWER
[ "$ANSWER" = s ] || exit 1
for portal in "${CHECKS[@]}"; do
  if ! jurisync-admin portal check "$OFFICE" "$portal"; then
    echo "Parado em $portal: corrija o acesso (novo pacote) antes de testar os demais." >&2
    exit 1
  fi
  sleep "$PAUSE"
done
jurisync-admin portal list "$OFFICE"
