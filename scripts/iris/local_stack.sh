#!/bin/bash
# Iris on this laptop — the same stack that runs in production, not a side-car.
#
# Two processes, because that IS Omnigent: a server, and a host that executes
# the tools. The `:8765` chat bridge is deliberately not used; it was a bespoke
# always-on HTTP server and it is being retired.
#
# Everything lives under ~/iris-local with its own data dir, so nothing here
# touches the production host daemon (ai.omnigent.host) already running on this
# machine and attached to omnigent.airbrx.ai.
#
#   scripts/iris/local_stack.sh    then open http://127.0.0.1:6768/iris
#
# Overridable: IRIS_LOCAL_PORT (6768), IRIS_LOCAL_HOME (~/iris-local),
# OMNIGENT_BIN (the checkout's .venv). The stack runs from THIS CHECKOUT, so
# whatever branch is checked out is what it serves — which is how a fix gets
# tested before it deploys.
#
# Port 6768, not 6767: on 2026-09-21 another agent session (Eva, hosted the
# same way Iris is) started its own `omnigent server --port 6767` from the uv
# tools install and took the port. Two servers on one port is not a draw — the
# survivor served an OLDER vendored Iris with no registered agent and no
# bindings, so sessions created against it vanished a moment later. Moved
# rather than fought over.
#   Ctrl-C stops both.
set -euo pipefail

# Resolve the checkout from this script's own location rather than a path that
# only exists on one laptop.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
OMNI="${OMNIGENT_BIN:-$REPO/.venv/bin/omnigent}"
PORT="${IRIS_LOCAL_PORT:-6768}"
STATE="${IRIS_LOCAL_HOME:-$HOME/iris-local}"
BIND="$STATE/iris-bindings.json"
export OMNIGENT_DATA_DIR="$STATE/data"
export OMNIGENT_IRIS_CONFIG="$BIND"

if [[ ! -x "$OMNI" ]]; then
  echo "no omnigent at $OMNI — set OMNIGENT_BIN, or create the venv in $REPO" >&2
  exit 1
fi
if [[ ! -f "$BIND" ]]; then
  echo "no tenant bindings at $BIND" >&2
  echo "copy $HERE/iris-bindings.example.json there and edit it: the example binds" >&2
  echo "the synthetic fixture only, which needs no credential and no live tenant." >&2
  exit 1
fi
# The login keychain is the secret store here, and it works in both directions
# because this host runs from an interactive session. OMNIGENT_DISABLE_KEYRING
# is deliberately NOT set: it is the Mac mini's workaround, where the host is a
# launchd daemon with no access to the login keychain, and setting it here
# instead points the read at an empty file store, so the live tenant's PAT
# resolves to nothing and only the fixture works.
mkdir -p "$OMNIGENT_DATA_DIR" "$STATE/workspaces/live" "$STATE/workspaces/fixture"

cleanup() { [[ -n "${HOST_PID:-}" ]] && kill "$HOST_PID" 2>/dev/null || true
            [[ -n "${SRV_PID:-}"  ]] && kill "$SRV_PID"  2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "· server  http://127.0.0.1:$PORT"
"$OMNI" server --host 127.0.0.1 --port $PORT > "$STATE/server.log" 2>&1 &
SRV_PID=$!
until curl -fsS -m 2 http://127.0.0.1:$PORT/api/version >/dev/null 2>&1; do
  kill -0 "$SRV_PID" 2>/dev/null || { echo "server died — see $STATE/server.log"; exit 1; }
  sleep 1
done
echo "  up: $(curl -fsS http://127.0.0.1:$PORT/api/version)"

echo "· host    (executes Iris's four tools)"
"$OMNI" host --server http://127.0.0.1:$PORT > "$STATE/host.log" 2>&1 &
HOST_PID=$!
# The binding's host_id has to match the host this server actually registered,
# and that id is only known once the host connects. Fill it in rather than ask
# an operator to copy a hex string by hand.
for _ in $(seq 1 40); do
  ID=$(grep -oE '\(([0-9a-f]{32})\)' "$STATE/host.log" 2>/dev/null | tr -d '()' | tail -1 || true)
  [[ -n "${ID:-}" ]] && break; sleep 1
done
if [[ -z "${ID:-}" ]]; then echo "host did not register — see $STATE/host.log"; exit 1; fi
python3 - "$BIND" "$ID" <<'PY'
import json, sys
p, hid = sys.argv[1], sys.argv[2]
rows = json.load(open(p))
for r in rows: r["host_id"] = hid
json.dump(rows, open(p, "w"), indent=2)
print(f"  host {hid[:12]} bound to both tenants")
PY
echo "· restarting server so it reads the bound config"
kill "$SRV_PID" 2>/dev/null || true; wait "$SRV_PID" 2>/dev/null || true
"$OMNI" server --host 127.0.0.1 --port $PORT >> "$STATE/server.log" 2>&1 &
SRV_PID=$!
until curl -fsS -m 2 http://127.0.0.1:$PORT/api/version >/dev/null 2>&1; do sleep 1; done

echo
echo "  Iris:  http://127.0.0.1:$PORT/iris"
echo "  logs:  $STATE/{server,host}.log"
echo "  Ctrl-C to stop both."
wait
