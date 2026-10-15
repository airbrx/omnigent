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
# Port 6768, and Iris and Eva now SHARE it (2026-09-24). They did not always:
# on 2026-09-21 Eva's session started its own `omnigent server --port 6767`
# from the uv tools install and took that port, and Iris moved rather than
# fight over it. Sharing one stack replaced that, deliberately.
#
# The reason the old collision is still worth reading: two servers on one port
# is not a draw. The survivor served an OLDER vendored Iris with no registered
# agent and no bindings, so sessions created against it reported "Session not
# found" moments later — which reads like data loss and is not. Before
# trusting this stack, check `lsof -nP -iTCP:6768 -sTCP:LISTEN` finds exactly
# one listener, and that GET /v1/iris returns a non-null agent_id.
#   Ctrl-C stops the server and both hosts.
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

# Eva shares this stack (2026-09-24, Abram's call: one stack, not two). The
# server registers her at STARTUP when OMNIGENT_EVA_CONFIG names a binding
# file, so this has to be exported before the first server start below — a
# late export lists nothing and looks like Eva is broken.
EVA_BIND="$STATE/eva-bindings.json"
EVA_HOME="$STATE/eva-host"
if [[ -f "$EVA_BIND" ]]; then export OMNIGENT_EVA_CONFIG="$EVA_BIND"; fi

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
            [[ -n "${EVA_HOST_PID:-}" ]] && kill "$EVA_HOST_PID" 2>/dev/null || true
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

if [[ -n "${OMNIGENT_EVA_CONFIG:-}" ]]; then
  # Eva's host: its own identity, data dir and config home, so IRIS's host
  # never resolves her token. It logs to its own file and starts AFTER the
  # host_id rewrite above, which takes the last id out of $STATE/host.log and
  # writes it to every Iris binding — if Eva's host shared that log, Iris's
  # tenants would be repointed at a host that has neither their workspaces nor
  # their PAT.
  #
  # The token never enters this process. Since omnigent #78/#80/#81 the
  # coordinator sends a runner launch frame carrying OUTREACH_MCP_URL as a
  # plain value (from the binding's base_url, omnigent/airbrx/eva/runtime.py)
  # and OUTREACH_MCP_TOKEN as the secret REFERENCE keychain:eva-outreach-token;
  # the host resolves that reference from its own keychain at launch, for that
  # one runner only, and only if the reference is named in
  # OMNIGENT_HOST_SECRET_REFS (omnigent/host/connect.py, resolve_agent_secret_env).
  # OMNIGENT_RUNNER_ENV_PASSTHROUGH is gone with it: nothing here is forwarded
  # host-wide any more. This is the design the RUNBOOK's "bridge" section
  # points at, and it works on a shared host too because #81 sends the
  # reference only for the host owner's own runners.
  if [[ ! -f "$EVA_HOME/host_id" ]]; then
    echo "· eva host SKIPPED: no host id at $EVA_HOME/host_id"
  else
    EVA_HOST_ID="$(cat "$EVA_HOME/host_id")"
    # Presence check only: the value is never printed, captured or exported.
    if ! "$(dirname "$OMNI")/python" -c 'import sys; from omnigent.onboarding.provider_config import resolve_secret; sys.exit(0 if resolve_secret("keychain:eva-outreach-token") else 1)' >/dev/null 2>&1; then
      # Say it and carry on. Eva listed with failing tools is a clearer state
      # than a stack refusing to start because one agent's secret is absent,
      # and Iris is unaffected either way.
      echo "· eva host SKIPPED: no keychain:eva-outreach-token; Iris is unaffected"
    else
      # Keep Eva's binding honest the same way the Iris one is kept: derive the
      # host_id rather than trust a hand-edited file to have kept up.
      python3 - "$EVA_BIND" "$EVA_HOST_ID" <<'EVAJSON'
import json, sys
path, host_id = sys.argv[1], sys.argv[2]
rows = json.load(open(path))
for row in rows:
    row["host_id"] = host_id
json.dump(rows, open(path, "w"), indent=2)
EVAJSON
      echo "· eva host (runs Eva's turns; outreach app at 127.0.0.1:8000)"
      OMNIGENT_HOST_ID="$EVA_HOST_ID" \
      OMNIGENT_HOST_NAME="$(hostname) (eva local)" \
      OMNIGENT_DATA_DIR="$EVA_HOME/data" \
      OMNIGENT_CONFIG_HOME="$EVA_HOME" \
      OMNIGENT_HOST_SECRET_REFS="keychain:eva-outreach-token" \
        "$OMNI" host --server "http://127.0.0.1:$PORT" --non-interactive \
          > "$STATE/eva-host.log" 2>&1 &
      EVA_HOST_PID=$!
    fi
  fi
fi

echo
echo "  Iris:  http://127.0.0.1:$PORT/iris"
[[ -n "${EVA_HOST_PID:-}" ]] && echo "  Eva:   http://127.0.0.1:$PORT (pick Eva in the agent drawer)"
echo "  logs:  $STATE/{server,host,eva-host}.log"
echo "  Ctrl-C to stop both."
wait
