# Iris in Omnigent

The integration is implemented in this checkout. Production rollout and hosted browser/model acceptance are pending; a passing local test is not deployment.

After rollout, open `https://omnigent.airbrx.ai/iris`, or **Agents → Iris → Open workspace**. The landing lists the account's tenants from their last captures. A tenant with a previous session of yours gets **Resume** (matched on host **and** workspace) beside **New**; otherwise its only button is **Open workspace**, which creates one session for that tenant. The workspace is v2 (`docs/iris/WORKSPACE_V2.md`): tabs for Overview, Findings, Rules, Proposals, Evidence, Results and Accounts, with **Ask Iris** docked on the right and collapsible to a rail. The chat survives a reload. A session with no capture collects its first overview automatically, once; after that the page offers **Collect now**, and **Collect a fresh overview** runs one on demand. A capture older than five minutes is shown with "may be out of date" while one background refresh runs. **Open native chat** continues that exact session. **Session downloads** lists the session's JSON/Markdown through the normal authenticated file routes. To start with native chat, choose the Iris row in the drawer, select the tenant, then **Start chat**. The selected host/workspace must match an operator binding.

## Architecture and ownership

`Agents drawer → registered /v1/agents Iris → POST /v1/sessions → native Claude SDK → four existing Iris tools → session-owned file APIs`.

The workspace is served inside the Omnigent shell at `/iris/{session_id}`, framed same-origin, with authenticated assets/API under `/v1/iris/sessions/{session_id}/ui/`. The page is `omnigent/airbrx/iris/ui/` on the shared workspace kernel (`omnigent/airbrx/workspace/ui/`, served as `ui/kernel/*`); the portrait and logo still come from the pinned archive. Nothing else is served from that path: no `iris-state.json`, no `demo-state.json`, and none of the pinned app's own files. Extensions V1 grants only `sessions.read`; it cannot submit or cancel turns or retrieve session files. This fork therefore adds one small shell page and an authenticated host router instead of widening extension permissions or inventing a portable AgentSpec field. The host-side Iris mapping uses the existing agent registry and workspace-scoped avatar store.

The pinned `airbrx/iris` revision and archive SHA256 are in `omnigent/airbrx/iris/source.json`. `scripts/iris/vendor.py /path/to/iris-app` reproduces the archive from tracked files at the recorded revision, regardless of the source checkout HEAD. Use `--revision REVIEWED_COMMIT` only when deliberately updating the pin. The original Python package, portrait, four tool modules and curated skills are unchanged. Packaging folds the existing skills into instructions and supplies a short catalog description. Private `ui/iris-state.json` is excluded. The archive still carries the pinned app's `ui/index.html`, `app.js`, `style.css` and `theme.js`, but the server no longer serves them; dropping them from `vendor.py`'s allowlist waits for the `airbrx/iris` change that marks `ui/` dev-only, and then a re-vendor (`WORKSPACE_V2.md`, section 7, step 5). The v2 workspace has no report import: everything it shows comes from a model turn in that session.

ToolManager exposes exactly `iris_overview`, `iris_investigate`, `iris_audit`, `iris_propose`. Native runner dispatch also denies unadvertised ambient tools before execution. The pinned spec and absolute tool-source bytes are checked before execution. SDK/dependency versions are locked by the `iris` extra. The host's configured model authentication remains in charge; the integration does not inject model keys or change subscription/API-key mode.

## Operator configuration

Install the same reviewed Omnigent revision on coordinator and execution host. Use `uv sync --frozen --extra iris --group dev` in this checkout. The Airbrx CD job now includes `--extra iris` when syncing the server.

Prepare a private JSON configuration file on **both** machines. It contains references, never credential values:

```json
[
  {
    "tenant_id": "selected-authorized-tenant",
    "host_id": "existing-host-id-from-v1-hosts",
    "workspace": "/absolute/dedicated/iris-workspace",
    "users": ["authorized-omnigent-user-id"],
    "pat_ref": "keychain:iris-selected-tenant",
    "fixture": false
  }
]
```

`scripts/iris/iris-bindings.example.json` is that file with only the fixture
row filled in, which is the safe thing to start from: it needs no credential
and names no live tenant.

Two operational scripts live beside it, checked in rather than kept on one
laptop, because a machine that is the only record of how production is
configured is a machine that can take that knowledge with it:

| | |
|---|---|
| `scripts/iris/configure_coordinator.sh` | Writes the coordinator's binding file and `OMNIGENT_IRIS_CONFIG` through SSM, then restarts it. Needs `aws sso login --profile airbrx-prod` first — it is the one part of the rollout an agent cannot do. Idempotent; re-running replaces the file and leaves one env line. `OMNIGENT_COORDINATOR_INSTANCE` and `AWS_REGION` override the defaults. |
| `scripts/iris/local_stack.sh` | Runs the same two processes locally — a server and a host — from **this checkout**, so whatever branch is checked out is what it serves. That is how a fix gets exercised before it deploys. `IRIS_LOCAL_PORT` (6768), `IRIS_LOCAL_HOME` (`~/iris-local`) and `OMNIGENT_BIN` override the defaults. |

The local stack defaults to port **6768**, not 6767. Two servers on one port is
not a draw: on 2026-09-21 another agent session took 6767 and the survivor
served an older vendored Iris with no registered agent and no bindings, so
sessions created against it reported "Session not found" moments later — which
reads like data loss rather than a port conflict. Before trusting a local
stack, confirm `GET /v1/iris` returns a non-null `agent_id` and a non-empty
`bindings`.

Use a separate, visibly synthetic binding/workspace for fixture acceptance (`fixture: true`, `pat_ref: ""`). Do not change tenant or fixture mode underneath a session. Create the execution directory beforehand. It is an operator-owned directory, not a writable agent bundle. Do not share it with unrelated code execution sessions.

On the execution host, store the existing authorized PAT with the supported Omnigent secret store. The prompt hides input; do not pass a PAT as a command argument:

```sh
.venv/bin/python scripts/iris/store_pat.py iris-selected-tenant
```

The `keychain:` resolver uses macOS Keychain, with Omnigent's existing private 0600 fallback when unavailable. Only the Iris tool subprocess receives the resolved PAT; no model token, runner-auth token, frontend payload or plist contains it. The coordinator only needs the reference and cannot resolve the host's keychain.

Set `OMNIGENT_IRIS_CONFIG` to the JSON file path on the coordinator (`/etc/omnigent/server.env`). Startup registers the pinned bundle idempotently and installs the existing portrait if no custom avatar exists. Restart the coordinator through the existing Airbrx deployment flow.

The Mac execution host runs launchd job **`ai.omnigent.host`**, started as
`python -m omnigent.host.service_entry --server https://omnigent.airbrx.ai --auto-upgrade`.
(An earlier `ai.airbrx.omnigent.host` job running `caffeinate -i omnigent host`
was replaced; its plist is kept under `~/.omnigent/plist-backups-*`.) Its
`HOME`/`PATH` environment cannot inherit a caller's exports, so the binding file
path has to be on the job itself:

```sh
cd /Users/abramerickson/projects/airbrx/omnigent
.venv/bin/python scripts/iris/prepare_host.py \
  --config /Users/abramerickson/.omnigent/iris-host.json \
  --output /Users/abramerickson/.omnigent/iris-host.prepared.plist
```

The script validates bindings, secret availability and four-tool registration,
then writes the proposed plist without installing or restarting it. Against the
installed job the result is a **two-key delta**, both non-secret:

| Key | Value | Why |
|---|---|---|
| `OMNIGENT_IRIS_CONFIG` | path to the binding file | Explicitly allowlisted in `_build_runner_env`; no broad `PYTHONPATH`/PAT passthrough is needed, and the runtime resolves `pat_ref` per invocation rather than trusting a launcher's environment. |
| `OMNIGENT_INSTALL_EXTRAS` | `iris` | The Iris tool subprocess runs on the host's **own** interpreter (`sys.executable`), so the extra's pinned `claude-agent-sdk`/`mcp`/`httpx`/`jsonschema` must survive an upgrade. A `--auto-upgrade` host re-installs by piping the server's `install.sh` with **no arguments**, so without this the extra is silently dropped on the next upgrade and Iris runs on whatever the base install happens to carry. |

The launcher itself is deliberately **not** repointed. `--auto-upgrade` installs
the coordinator's build and re-execs, which is how this host tracks a deploy;
pinning it at a development checkout's venv disables that silently. Use
`--launcher` only when taking a host off auto-upgrade on purpose.

After approving the rollout and finishing active work on that host:

```sh
cp ~/Library/LaunchAgents/ai.omnigent.host.plist ~/.omnigent/iris-host.original.plist
cp ~/.omnigent/iris-host.prepared.plist ~/Library/LaunchAgents/ai.omnigent.host.plist
launchctl bootout gui/$(id -u)/ai.omnigent.host
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.omnigent.host.plist
```

Restart interrupts sessions served by this host; the service then reconnects to
the existing coordinator. Do not use broad `pkill` commands. To roll back the
launcher, restore the saved original plist and repeat the two launchctl
commands. The code/config rollout must be coordinated; a version endpoint alone
does not prove success.

## Workspace v2 and rollback

v2 is the only Iris workspace. The cutover (W5) removed the pinned UI path and
`omnigent/airbrx/iris/host.js`, the adapter that was injected into it, with its
test. `OMNIGENT_IRIS_UI` is no longer read: it can be left in
`/etc/omnigent/server.env` or removed, and setting it to `v1` or anything else
does **not** bring the old workspace back. There is nothing to bring back
without `host.js`: the pinned app on its own falls back to
`iris-state.json` and then the synthetic `demo-state.json` and answers from them
locally, which is exactly what the host refuses to serve.

**Rollback** is a code rollback, not a setting:

1. `git revert <cutover merge commit>` on `main` (it restores `host.js`, its
   test, and the switch with the pinned UI as the default), then deploy that
   through the normal push to `omnigent-airbrx-server`.
2. Or redeploy the previous server build. The vendored archive did not change
   in the cutover, so either route serves the same pinned package.
3. After either, `GET /v1/iris/sessions/{id}/ui/` should contain
   `<script src="host.js">`; with the cutover in place it contains
   `<script src="app.js">` and `ui/host.js` is 404.

No data migrates in either direction: sessions, reports and the chat history
belong to the Omnigent session, not the UI.

## Verification

```sh
uv run --no-sync pytest tests/airbrx tests/server/integration/test_iris_host.py tests/runner/test_local_tool_workspace_binding.py tests/runner/test_app_spec_workdir_paths.py
cd web
pnpm exec vitest run src/shell/AgentDrawer.test.tsx src/shell/IrisWorkspace.test.tsx src/shell/IrisAccountView.test.tsx src/shell/irisWorkspaceApp.test.ts src/shell/workspaceKernel.test.ts
pnpm run lint
pnpm run type-check
pnpm run build
```

Repository checks: `uv run --no-sync pre-commit run --all-files`.

After deployment, verify actual native dispatch/downloads with the existing CLI login:

```sh
.venv/bin/python scripts/iris/verify_host.py \
  --server https://omnigent.airbrx.ai --tenant fixture-iris \
  --output /private/path/iris-fixture-acceptance.json
# Only for the explicitly selected authorized live tenant:
.venv/bin/python scripts/iris/verify_host.py \
  --server https://omnigent.airbrx.ai --tenant SELECTED_TENANT --live \
  --output /private/path/iris-live-acceptance.json
```

The verifier leaves its two sessions available for browser review. It records deployment revision, Iris revision, real session/mount, called tool, downloaded content hashes and cross-session 404. It does not claim browser QA or all-tool/model coverage from an HTTP result. Use the shared Iris `METAHARNESS-VERIFICATION.md` for the remaining cases. Capture actual hosted light/dark, narrow viewport, Tab/Shift+Tab/Escape, cancellation/restart, failure and download workflows for the PR demo; none has been marked passed from a standalone preview.

## Recovery and current limits

- **Busy/cancel:** one workspace turn at a time. Cancel uses the native interrupt event. The tool subprocess is killed on cancellation or deadline; durable reservations are retained. Open native chat to inspect/retry.
- **Credentials unavailable/expired:** the tool fails visibly; repair the host secret reference and start a fresh session. No fallback model loop is launched.
- **Budget exhausted:** the existing Iris budget persists across the conversation, including its 300-second elapsed budget. Start a new session; reload does not reset it.
- **Refresh failed:** a fresh refresh must produce a new successful overview tool result. Old reports do not become fresh evidence. The UI retains its prior capture; captures older than five minutes are labelled "may be out of date" and get one background refresh, at most once per capture. A failed refresh does not retry by itself.
- **Isolation:** the UI checks current host authentication, edit permission, registered agent and authorized user/host/workspace binding. State reads only reports referenced by native Iris tool results. Downloads use the existing session/workspace permission and file ownership checks. A report's tenant must match the binding.
- **Workspace UI authentication:** the framed workspace authenticates with the host's same-origin cookies (`credentials: "same-origin"` on every kernel request), and nothing passes a token into the frame. The normal web deployment supports this. An embedded client that reaches the API only through its own `fetcher` cannot route an `<iframe src>` through it, so there the shell does not mount the frame: it says the workspace is served by the Omnigent host and offers **Open native chat instead**, which is the same session. There is no token bridge for the Iris workspace, and none is planned; Eva's token bridge (`docs/eva/RUNBOOK.md`) is a runner credential, not a document transport.
- **Monitoring:** unavailable. No scheduler, browser-closed watch, anomaly detector or protective action is claimed. Shared article-alignment work remains open.

## Rollout gate

Observed deployment before this change: `0.13.0`, `6359361d297f8338611f783d3e164e087797f688`, authenticated at `https://omnigent.airbrx.ai`. Iris was absent from its drawer. New production code/config, host restart, actual SDK fixture/live turns and hosted UI screenshots remain unverified until rollout approval. Push to `omnigent-airbrx-server` triggers production CD; a review branch/PR does not. No production deployment is represented by the local test evidence.
