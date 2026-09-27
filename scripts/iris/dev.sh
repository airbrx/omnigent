#!/bin/bash
# The standalone Iris viewer on this laptop: saved runs, dev sign-in, no agent.
#
#   scripts/iris/dev.sh [--open]      (or: just iris-dev)
#   then open http://127.0.0.1:6790/iris
#
# Serves the packaged synthetic fixture, plus ~/.iris-viewer/runs when it
# exists, plus any IRIS_VIEWER_RUNS (colon-separated). Loopback only.
# Overridable: IRIS_VIEWER_PORT (6790), IRIS_VIEWER_DEV_EMAIL, IRIS_VIEWER_RELOAD=1.
# Idempotent: if this checkout's viewer already listens, it prints the URL and
# exits 0. Any other listener on the port: it names it and exits 1. It never
# kills anything. Ctrl-C stops the viewer.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PORT="${IRIS_VIEWER_PORT:-6790}"
URL="http://127.0.0.1:$PORT/iris"
OPEN=0
for arg in "$@"; do
  case "$arg" in
    --open) OPEN=1 ;;
    *) echo "usage: scripts/iris/dev.sh [--open]" >&2; exit 2 ;;
  esac
done

open_browser() {
  if [ "$OPEN" = 1 ]; then
    if command -v open >/dev/null; then open "$URL"; else xdg-open "$URL" >/dev/null 2>&1 || true; fi
  fi
}

LISTENER="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -Fp 2>/dev/null | sed -n 's/^p//p' | head -1 || true)"
if [ -n "$LISTENER" ]; then
  HEALTH="$(curl -fsS --max-time 3 "http://127.0.0.1:$PORT/healthz" 2>/dev/null || true)"
  if printf '%s' "$HEALTH" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
sys.exit(0 if d.get("app") == "iris-viewer" and d.get("checkout") == sys.argv[1] else 1)
' "$REPO"; then
    echo "Iris viewer already running from this checkout: $URL"
    open_browser
    exit 0
  fi
  echo "Port $PORT is taken by pid $LISTENER: $(ps -o command= -p "$LISTENER" 2>/dev/null || echo unknown)" >&2
  echo "Nothing was stopped. Free the port, or set IRIS_VIEWER_PORT." >&2
  exit 1
fi

ROOTS=(--runs "$REPO/omnigent/airbrx/iris/fixtures/runs")
if [ -d "$HOME/.iris-viewer/runs" ]; then
  ROOTS+=(--runs "$HOME/.iris-viewer/runs")
fi
if [ -n "${IRIS_VIEWER_RUNS:-}" ]; then
  IFS=: read -r -a EXTRA <<< "$IRIS_VIEWER_RUNS"
  for root in "${EXTRA[@]}"; do
    [ -n "$root" ] && ROOTS+=(--runs "$root")
  done
fi

# A clean environment: the viewer never inherits CLAUDE_CODE_* or any token.
CLEAN=(HOME="$HOME" PATH="$PATH" USER="${USER:-}" LANG="${LANG:-C.UTF-8}")
for name in IRIS_VIEWER_DEV_EMAIL IRIS_VIEWER_RELOAD IRIS_VIEWER_PUBLIC_URL; do
  if [ -n "${!name:-}" ]; then CLEAN+=("$name=${!name}"); fi
done

if [ "$OPEN" = 1 ]; then
  ( for _ in $(seq 1 60); do
      curl -fsS --max-time 1 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && { open_browser; exit 0; }
      sleep 0.5
    done ) &
fi

cd "$REPO"
exec env -i "${CLEAN[@]}" uv run --frozen --project "$REPO" python -m omnigent.airbrx.iris.viewer \
  --host 127.0.0.1 --port "$PORT" --auth dev "${ROOTS[@]}"
