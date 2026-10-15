# Iris standalone viewer

Iris runs on this laptop with no agent, as a results viewer: you open saved
runs, per tenant and ISO week, and click through all 7 tabs. It is the same
app that is framed in Omnigent (`omnigent/airbrx/iris/ui/**` and the shared
kernel, byte-identical); only the host seams differ. The contract is
`STANDALONE_VIEWER.md` (Iris CoS, 27 Sep 2026), which this module cites by
section.

## Run it

```bash
scripts/iris/dev.sh          # or: just iris-dev
# then open http://127.0.0.1:6790/iris   (/ works too)
scripts/iris/dev.sh --open   # also opens the browser
```

- **Port 6790**, loopback only. Override with `IRIS_VIEWER_PORT`.
- **Idempotent.** If this checkout's viewer already listens on the port, it
  prints the URL and exits 0. Any other listener: it prints that pid and
  command and exits 1. It never kills anything.
- **Runs served:** the packaged synthetic fixture
  (`omnigent/airbrx/iris/fixtures/runs`), plus `~/.iris-viewer/runs` when it
  exists, plus any `IRIS_VIEWER_RUNS` (colon-separated). A tenant id found in
  two roots stops the viewer at startup, naming both roots.
- **Clean environment.** The viewer starts under `env -i` with only `HOME`,
  `PATH`, `USER`, `LANG` and the named `IRIS_VIEWER_*` variables, so it never
  inherits `CLAUDE_CODE_*` or any token.
- **Dev sign-in.** Every request is `IRIS_VIEWER_DEV_EMAIL` (default
  `dev@localhost`). There is no password and no cookie. The viewer refuses to
  start with dev sign-in on any bind but `127.0.0.1`, `::1` or `localhost`, or
  when `IRIS_VIEWER_PUBLIC_URL` is https, and it refuses requests whose `Host`
  is not loopback.
  The email is shown on the page and in `api/host`, so keep it a placeholder,
  not a real address.
- **Edits.** UI files are read from disk per request with `no-store`, so an
  edit to `iris/ui/**` or `workspace/ui/**` shows on a reload.
  `IRIS_VIEWER_RELOAD=1` adds uvicorn `--reload` for Python edits.
- Ctrl-C stops it. Left running in the background instead:
  `kill $(lsof -nP -iTCP:6790 -sTCP:LISTEN -t)`.

The module form is `python -m omnigent.airbrx.iris.viewer --host 127.0.0.1
--port 6790 --auth dev --runs ROOT [--runs ROOT ...]`.

## What it shows

- The header badge reads **Agent not connected**. Chat history is the run's
  saved messages and is read-only; the composer, the ask chips and "Collect a
  fresh overview" are disabled.
- A week picker lists the tenant's runs, newest first; a partial week reads
  "partial (3 of 7 days)", and an unreadable run is listed and disabled.
- Q19 is unchanged: a partial week renders with its coverage, and an
  inconsistent capture is refused ("This capture is not shown").
- Accounts ranks each tenant's newest readable run with the pinned
  `iris.account.rank`, reading only `tenant_id` and `metrics` from its report.
  "Open" goes to that tenant's newest run.

## Get real runs

```bash
python -m omnigent.airbrx.iris.export --dry-run     # what would be written
python -m omnigent.airbrx.iris.export               # writes ~/.iris-viewer/runs
```

The exporter reads the local Omnigent stack (`http://127.0.0.1:6768`) only
(Decision D-2 (a)) and refuses an output path inside any git worktree. Restart
nothing: the viewer rescans its roots on every request.

## Routes

| Route | Answers |
|---|---|
| `GET /`, `/iris` | 302 to the first tenant's (by name) newest readable run, or "No saved runs in ROOTS" |
| `GET /t/{tenant}/` | 302 to that tenant's newest readable run |
| `GET /t/{tenant}/{week}/ui/{asset}` | the shared `ui_assets.ui_response` rules; everything else 404 |
| `GET …/ui/api/host` | the host descriptor: `agent.connected: false`, `data.source: "runs"`, `data.weeks` |
| `GET …/ui/api/state` | `state.json` with only `cache_age_seconds` and `stale` recomputed; 403 when its tenant is not the directory's |
| `GET …/ui/api/readiness`, `api/session`, `api/items`, `api/files[/{id}/content]` | the saved run, in the native shapes |
| `POST …/ui/api/{chat,refresh,cancel}` | 409 "Iris is not connected to this viewer; nothing was run." |
| `GET /api/tenants`, `/api/account` | the `/v1/iris` and `/v1/iris/account` shapes |
| `GET /healthz` | `{ok, app: "iris-viewer", checkout, roots, tenants, runs, unreadable}` |

Every tenant, week and file id is matched against the run layout's patterns
before it touches the disk, symlinks are not followed, and a run whose files
do not match `manifest.json` checksums is never served (it is listed as
unreadable and counted by `/healthz`).

## Rules the tests hold

`tests/airbrx/test_iris_viewer.py`: importing the viewer never loads
`omnigent.server` or `omnigent.airbrx.iris.routes`; dev sign-in refuses
non-loopback binds, https public URLs and non-loopback `Host` headers; path
parts off the layout are 404; tampered runs are not served; state equals the
saved body except the two clock fields; the only non-read routes are the three
409 refusals; and `dev.sh` is idempotent and never stops another listener.
