# Tally runbook

Tally is chief of staff for the Airbrx agent and dashboard control plane: the
third workspace agent on omnigent.airbrx.ai, beside Iris and Eva. She answers
questions about agents, dashboards, costs, coverage, open decisions and
blockers, and says what is measured, what is estimated and what is unavailable.

**v1 is read-only**, by Abram's decision. She has one MCP server and four reads,
holds no credential beyond the one read token, and cannot change policy,
routes, dashboards, Superset or gateway rules. A write needs a separately
approved tool boundary.

## What is where

| Piece | Path |
|---|---|
| Bundle (spec and instructions) | `omnigent/airbrx/tally/bundle/{config.yaml,AGENTS.md}` |
| Bindings (`OMNIGENT_TALLY_CONFIG`) | `omnigent/airbrx/tally/config.py` |
| Token reference to runner env | `omnigent/airbrx/tally/runtime.py` |
| Tool boundary | `omnigent/airbrx/tally/policy.py` |
| Catalog and readiness routes | `omnigent/airbrx/tally/routes.py` (`/v1/tally`, `/v1/tally/readiness`) |
| Workspace adapter and portrait route | `omnigent/airbrx/tally/workspace.py` |
| Framed workspace app | `omnigent/airbrx/tally/ui/` |
| Web landing | `web/src/shell/TallyWorkspace.tsx` (`/tally`, `/tally/:sessionId`) |
| Harness protections | `omnigent/inner/claude_sdk_harness.py` (`_is_tally_agent`) |

The workspace tabs frame the portal's own pages through the existing
same-origin gateway proxy (`omnigent/airbrx/gateway/proxy.py`):
Analytics `/gateway/app/#overview`, Agent management `/gateway/app/#agents`,
Sprint board `/gateway/app/#board`. The home view's KPIs (decisions waiting,
blockers, agents tracked, data freshness, estimated API-equivalent spend) are derived from Tally's recorded tool
results with no model call; a value she has not read, or the portal did not
report, shows as a dash with the reason, never as zero.

## Tools

One server, `portal`, a Streamable HTTP MCP endpoint served by the gateway
router portal on Abram's Mac at `http://127.0.0.1:4319/mcp`, loopback on the
same Mac as her host (exactly `/mcp`, no trailing slash; POST JSON-RPC, 405 on
GET, `Authorization: Bearer <token>`).

| Tool | Arguments |
|---|---|
| `get_analytics_overview` | none |
| `get_agent_policy` | `agent`: `iris` or `eva` |
| `get_sprint_board` | none |
| `get_health` | none |

Three refusals, outermost first: the bundle's `tools:` allow list; the
`tally-tool-boundary` guardrail, which also rejects unknown argument names and
any `agent` outside `iris`/`eva`; the portal's own MCP layer. The claude-sdk
harness adds `--strict-mcp-config` and removes the `Skill` tool for her by
name, as it does for Iris and Eva, so the operator's claude.ai connectors never
reach her CLI.

## Configuration at deploy time

On the coordinator, `/etc/omnigent/server.env` carries only non-secret values:

```
OMNIGENT_TALLY_CONFIG=/etc/omnigent/tally.json
TALLY_PORTAL_MCP_URL=http://127.0.0.1:4319/mcp
TALLY_PORTAL_MCP_TOKEN=placeholder-for-bundle-validation-only-runners-expand-their-own
```

The last two exist so the bundle validates on the coordinator; the placeholder
is not a token and never reaches a runner, which expands its own values.

`/etc/omnigent/tally.json`:

```json
[
  {
    "label": "live",
    "users": ["aerickson@airbrx.com"],
    "base_url": "http://127.0.0.1:4319",
    "token_ref": "keychain:tally-portal-token",
    "host_id": "3d59945073fa4bb4beee19cec305e45f",
    "workspace": "/Users/abramerickson/.omnigent-tally-host/workspace"
  }
]
```

Allowed keys: `users`, `host_id`, `base_url`, `token_ref`, `label`, `fixture`,
`workspace`. Unknown keys stop startup. A live binding needs a `token_ref`.
Without `OMNIGENT_TALLY_CONFIG` Tally is inert: not registered, catalog empty,
readiness 404. Unbinding is the rollback.

`base_url` is loopback *on the host*, not on the coordinator: the runner on the
Mac dials it. The reference `keychain:tally-portal-token` resolves on the Mac,
and only because her host lists it in `OMNIGENT_HOST_SECRET_REFS` (next
section).

The runner receives `TALLY_PORTAL_MCP_URL` (value) and `TALLY_PORTAL_MCP_TOKEN`
(resolved from the reference); the bundle expands those two, on the runner only
(`env_expansion: runner`). The coordinator's routes never hold the token.

## Where Tally runs

Tally runs on her own Omnigent host on Abram's Mac, beside the portal she
reads, as Iris and Eva do (Abram, September 26, 2026: "Tally should run
locally where we develop"). The coordinator registers her and runs no agents.
Model access is the Mac's own Claude subscription login, as for Iris and Eva;
no model token is injected anywhere.

### Why a host is needed at all (verified in code)

1. `launch_env` (`omnigent/airbrx/tally/runtime.py`) returns the URL and the
   token *reference* for a binding with or without `host_id`. `host_id` plays
   no part in it, exactly as in Eva's provider.
2. That answer reaches a runner only through `launch_env_fields`
   (`omnigent/server/routes/_host_launch.py:224`), which is spread into every
   host launch frame: session create with a host
   (`omnigent/server/routes/sessions/routes_core.py:488`),
   `POST /v1/hosts/{id}/runners` (`omnigent/server/routes/hosts.py:1119`) and the
   relaunch path (`omnigent/server/routes/_sessions/helpers.py:5607`). It is
   withheld unless the acting user is the host's owner (`_host_launch.py:298`).
3. The host resolves the reference in `resolve_agent_secret_env`
   (`omnigent/host/connect.py:988`, applied at `connect.py:2185`), only if the
   host's `OMNIGENT_HOST_SECRET_REFS` (`connect.py:971`) lists it.

So **a session created with no `host_id` and no managed sandbox gets no runner
at all.** Session create launches one only when a host is named
(`routes_core.py:723`) or `host_type` is `managed` (`routes_core.py:709`), and
dispatch refuses a session with no bound runner
(`omnigent/runner/routing.py:134`, `:183`). The binding therefore names her
host, and the landing creates sessions on it. Because the binding has a
`host_id`, `is_host_local()` treats the loopback `base_url` as another
machine's and the coordinator readiness probe steps aside; check the portal on
the Mac instead (`curl -i http://127.0.0.1:4319/mcp`, expect 405).

### The host (Eva's bridge pattern)

A user LaunchAgent, `ai.omnigent.tally-host`
(`~/Library/LaunchAgents/ai.omnigent.tally-host.plist`, `KeepAlive`,
`RunAtLoad`), runs `~/.omnigent-tally-host/tally_host.sh`, which exports:

```
OMNIGENT_HOST_ID=<contents of ~/.omnigent-tally-host/host_id>   # 3d59945073fa4bb4beee19cec305e45f
OMNIGENT_HOST_NAME="Abrams-MacBook-Air.local (tally)"
OMNIGENT_DATA_DIR=~/.omnigent-tally-host/data
OMNIGENT_CONFIG_HOME=~/.omnigent-tally-host
OMNIGENT_HOST_SECRET_REFS=keychain:tally-portal-token
```

and execs `omnigent host --server https://omnigent.airbrx.ai
--non-interactive`. No `--auto-upgrade`: the Iris host upgrades the shared
install. Neither the script nor the plist holds a secret; the host resolves
the keychain reference itself at runner launch.

### The portal

A second user LaunchAgent, `ai.airbrx.gateway-portal`, runs the gateway router
portal on `127.0.0.1:4319` from a copy at `~/.airbrx-portal/app`, because
launchd cannot read `~/Documents` (it syncs through iCloud).
`~/.airbrx-portal/sync.sh` refreshes the copy: code and evidence only, never
connection files. The portal reads `AIRBRX_TALLY_MCP_TOKEN` from the keychain
entry `tally-portal-token` (the omnigent secret store) at start, so host and
portal share one entry and cannot drift.

### Setting it up (once, on the Mac)

Nothing below prints a secret.

1. Store the portal read token in the keychain through the omnigent secret
   store under the name `tally-portal-token` (value pasted by Abram, never on
   a command line that lands in shell history).
2. Create `~/.omnigent-tally-host/` with `host_id` (a uuid4 hex, generated
   once), `data/`, `workspace/` and the wrapper `tally_host.sh` above, then
   install and load `ai.omnigent.tally-host`
   (`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/ai.omnigent.tally-host.plist`).
   The host signs in with Abram's Omnigent login so it belongs to him (a
   reference reaches only sessions on a host its user owns; never `--shared`).
3. Copy the portal with `~/.airbrx-portal/sync.sh`, then load
   `ai.airbrx.gateway-portal` the same way. Check
   `curl -i http://127.0.0.1:4319/mcp` answers 405.
4. Confirm the host is online (`GET /v1/hosts`).
5. On the coordinator, write the binding and `server.env` values above and
   restart `omnigent-server`.
6. Open Tally from the drawer and ask "what is blocked right now".

### Operating it

- **Rotate the token:** store a new value under `tally-portal-token`, then
  restart both agents so each rereads it:
  `launchctl kickstart -k gui/$UID/ai.airbrx.gateway-portal` and
  `launchctl kickstart -k gui/$UID/ai.omnigent.tally-host`.
- **Update the portal:** `~/.airbrx-portal/sync.sh`, then kickstart
  `ai.airbrx.gateway-portal`.
- **Restart:** `launchctl kickstart -k gui/$UID/<label>`.
- **Stop:** `launchctl bootout gui/$UID/<label>`.
- **Logs:** the host's is `~/.omnigent-tally-host/data/logs/launchd.log`; the portal's is `~/.airbrx-portal/logs/portal.log`.

**Availability is a laptop's.** Both agents live in Abram's GUI domain: up
while he is logged in, not after a reboot until he logs in. While the Mac
sleeps Tally has no host, so a session started then finds no runner; the host
reconnects on wake. As with Eva, the first session after a host restart can
fail once on a cold zygote fork; retry before diagnosing. Without the portal,
Tally has a host and no tools, so check the 405 above before the token.

### Verified, 2026-09-26

- Her first answer came from real portal data.
- Her CLI init listed only the `omnigent` MCP server: no claude.ai connectors,
  no `Skill`.
- Omnigent framework `sys_*` tools are offered to her but denied by her
  `tool_boundary` server-side, before execution.

### Tried and retired: a coordinator host

The same night, Tally first ran on an Omnigent host on the coordinator itself,
next to a portal sidecar there. It was retired within hours: it needed a
separate Claude login on the server (a `CLAUDE_CODE_OAUTH_TOKEN` in a host env
file), which is not the Iris and Eva pattern and would have broken the
coordinator's "no model keys, runs no agents" rule. Nothing of it remains in
the live setup.

## Tool results

What the workspace reads from each tool's `structuredContent`:

- `get_analytics_overview`: the analytics object plus `run_count`. Agents
  tracked is `len(agents)`; data freshness is `updated_at`.
  `summary.api_equivalent_usd` is shown as **estimated** spend at published
  rates, never as a charge; `actual_charges_usd` is the only measured charge
  and shows as unavailable while it is `null`.
- `get_sprint_board`: `{"markdown", "truncated"}`. Decisions waiting counts the
  top-level list items under `## Decisions needed from Abram`; blockers counts
  the rows of the table under `## Blockers`. A missing section, or a Blockers
  section with no table, is unavailable, not zero.
- `get_agent_policy`: desired, applied and observed state, `audit` and
  `storage`, shown as read.
- `get_health`: `status`, `mode` and, when hosted, `hosting` and
  `analytics_source`, shown as read.

## Readiness

`GET /v1/tally/readiness` probes `GET {base_url}/mcp` with no credential:

- `reachable`: the portal answered at all.
- `mcp_mounted`: `true` only on the portal's 405; `false` on 404; `null` otherwise.
- `token_accepted`: always `null`. This route never holds her token; the first
  turn is the real check.

The per-session `/v1/tally/sessions/{id}/ui/api/readiness` lists what has been
verified and what has not, with Iris's keys.

## Portrait

`omnigent/airbrx/tally/assets/tally-portrait.png` is the Airbrx logomark, an
orange "A" on the `#101419` ground Eva's mark uses, drawn by
`scripts/tally/make_portrait.py` (1254x1254). It is a placeholder, not a
likeness. **Abram will supply a real portrait**; replace the file and delete the
script when he does. The server stores it in the avatar store on first start
only if no `tally` avatar exists, so an uploaded avatar wins.

## Verifying by hand

1. Start a local server with a binding for your user, pointed at a portal that
   serves `/mcp`, and a host whose environment carries the token and allows the
   reference as above.
2. Open the agent drawer: Tally is listed with the "A" mark and an "Open Tally
   workspace" button.
3. Open the workspace, press **Refresh from portal**. The KPI tiles fill from
   her reads; anything the portal did not report stays a dash with a reason.
4. Ask her to change Eva's policy. She should refuse, say she is read-only in
   this version, and say it would take a separately approved tool boundary.
