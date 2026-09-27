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
blockers, agents tracked, data freshness) are derived from Tally's recorded tool
results with no model call; a value she has not read, or the portal did not
report, shows as a dash with the reason, never as zero.

## Tools

One server, `portal`, a Streamable HTTP MCP endpoint served by the portal
sidecar on the coordinator at `http://127.0.0.1:4318/mcp` (exactly `/mcp`, no
trailing slash; POST JSON-RPC, 405 on GET, `Authorization: Bearer <token>`).

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

On the coordinator (`/etc/omnigent/server.env`):

```
OMNIGENT_TALLY_CONFIG=/etc/omnigent/tally.json
```

`/etc/omnigent/tally.json`:

```json
[
  {
    "label": "live",
    "users": ["<omnigent user id>"],
    "base_url": "http://127.0.0.1:4318",
    "token_ref": "env:AIRBRX_TALLY_MCP_TOKEN"
  }
]
```

Allowed keys: `users`, `host_id`, `base_url`, `token_ref`, `label`, `fixture`,
`workspace`. Unknown keys stop startup. A live binding needs a `token_ref`.
Without `OMNIGENT_TALLY_CONFIG` Tally is inert: not registered, catalog empty,
readiness 404. Unbinding is the rollback.

On whichever machine launches her runner (see the next section), the token
reference must resolve and must be allowed:

```
AIRBRX_TALLY_MCP_TOKEN=<the portal read token, never in a bundle or plist>
OMNIGENT_HOST_SECRET_REFS=env:AIRBRX_TALLY_MCP_TOKEN
```

The runner receives `TALLY_PORTAL_MCP_URL` (value) and `TALLY_PORTAL_MCP_TOKEN`
(resolved from the reference); the bundle expands those two, on the runner only
(`env_expansion: runner`). The coordinator's routes never hold the token.

## Where Tally runs

Abram's decision is that Tally runs with no `host_id`, next to the portal on
the coordinator. What the code does with that, verified:

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

The consequence: **a session created with no `host_id` and no managed sandbox
gets no runner at all.** Session create launches one only when a host is named
(`routes_core.py:723`) or `host_type` is `managed` (`routes_core.py:709`), and
dispatch refuses a session with no bound runner
(`omnigent/runner/routing.py:134`, `:183`). The coordinator process does not
run turns itself. Eva's hostless shape has the same property; its config
docstring's "the coordinator dials the app itself" is about the readiness
probe, not about who runs turns.

So "Tally runs on the coordinator" needs an execution host process
(`omnigent host`) running on the coordinator box, owned by the user who opens
Tally. Two shapes work with this code, and neither needs a code change:

- **Name that host in the binding** (`"host_id": "<coordinator host id>"`,
  `"workspace": "/abs/dir"`). The landing then creates sessions on it and the
  reference resolves there. `base_url` stays `http://127.0.0.1:4318`, which is
  the coordinator's loopback because the host is on the coordinator. Note that
  `is_host_local()` then reports the loopback as another machine's and the
  coordinator readiness probe steps aside; check with `curl -i
  http://127.0.0.1:4318/mcp` (expect 405) on the box instead.
- **Keep the binding hostless** and create sessions on that host another way
  (for example the native new-session picker). The workspace adapter accepts a
  hostless binding on any host (`workspace.py:436`, Eva's rule). The landing's
  Start, as copied from Eva, creates a hostless session for a hostless
  binding, which will not run.

This is a finding to decide on, not something this change resolves.

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
