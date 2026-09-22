# Eva in Omnigent

The integration is implemented in this checkout and **is not deployed**. A
passing local test is not deployment, and this document says at each step which
parts an agent did and which parts a person has to do.

After rollout, a person opens `https://omnigent.airbrx.ai`, sees **Eva** in the
agent drawer beside Iris, selects her, and starts a chat. Her tools are the
airbrx-outreach MCP surface, so what she can do is exactly what that rep could
do in the outreach web app, under that rep's own permissions.

## The precondition that is not about Eva

**Eva must not be bound to an outreach app that has never synced the CRM.**

On 2026-09-21 the outreach app was reachable, healthy, reporting 25 of 25 tables
and zero warnings, and serving eight fabricated leads whose addresses were all
`@*.example`. `airbrx.app.sync_runs` was `0`: no sync had ever run. Everything
anyone checked said ok, because nothing anyone checked asked the right question.

`GET /v1/eva/readiness` is the question, and `carries_crm_data` is the field. Do
not complete a rollout while it reads `false` or `null`. An agent in front of
seed data is the same defect with a larger audience.

## Architecture, and what it deliberately does not have

`Agents drawer -> registered /v1/agents Eva -> POST /v1/sessions -> omni meta
harness -> outreach MCP tools over HTTP`.

Iris binds a tenant to a named **execution host** which holds a PAT in its macOS
keychain, ships a pinned 2.3MB source archive verified by sha256 and extracted
per process onto `sys.path`, and needs a two-key launchd delta so an
auto-upgrading host keeps her extra. Every part of that exists because **her four
tools are local Python that has to run on a specific machine.**

Eva has **no local tools**. Every one is a remote MCP call to the outreach app.
So Eva has:

| Iris | Eva |
|---|---|
| vendored `iris-source.zip` + `source.json` digest | nothing; two tracked files read where they lie |
| `host_id` in the binding | none |
| `pat_ref` resolved from a machine keychain | a token reference resolved at the point of use |
| `scripts/iris/{vendor,store_pat,prepare_host,verify_host}.py` | none |
| a launchd plist delta on the Mac mini | none |

`tests/airbrx/test_eva_bundle.py::test_every_tool_is_remote_so_no_execution_host_is_needed`
pins this. If `local_tools` ever stops being empty, the whole apparatus above
comes back with it and somebody should have to argue for it first.

**This is also why it does not matter which of Abram's Macs is awake.** Eva's
brain runs on the coordinator.

## The model

`executor.type: omnigent`, the meta harness, `smart_routing_harness: auto`, and
**no model pinned**. There is no `llm:` block, because that block requires an
explicit `model` and naming one would pin every rep to it. Iris pins
`claude-sonnet-4-6`; Eva resolves whatever provider the coordinator is
configured with.

## The tool boundary, three deep

1. The bundle's `tools.outreach.tools` allow list, 15 of the 17 tools in
   `contracts/mcp_tools.md`.
2. `omnigent.airbrx.eva.policy.tool_boundary`, registered as a `tool_call`
   guardrail, fail-closed.
3. The outreach app's own tool layer, which is the authoritative one.

Two tools are withheld by name, and both are product refusals rather than
permission settings:

- **`approve_draft`**: approval belongs to the lead owner or an admin and never
  to the draft's author. Eva writes drafts.
- **`mark_sent`**: nothing in this system sends. A rep sends from their own
  client and then records that they did. An agent marking a draft sent would be
  recording an event it cannot observe.

Falsified rather than asserted: neutering `tool_boundary` to always ALLOW turns
red exactly the four denial cases and nothing else.

## Operator configuration

Bindings are non-secret: who may open Eva, where the outreach app is, and a
*reference* to the MCP bearer token. Never a token value.

```json
[
  {
    "label": "live",
    "users": ["aerickson@airbrx.com"],
    "base_url": "https://eva.airbrx.ai",
    "token_ref": "env:OUTREACH_MCP_TOKEN",
    "fixture": false
  }
]
```

Unknown keys are refused rather than ignored, so a typo in an authorization file
stops startup instead of silently widening or narrowing who can open the agent.
A `host_id` is an unknown key here, deliberately, and its rejection is tested.

`base_url` must be the outreach app **as the coordinator can reach it**. A
`localhost` URL binds Eva to a machine that is not the one serving her.

Set `OMNIGENT_EVA_CONFIG` on the coordinator in `/etc/omnigent/server.env`.
`scripts/eva/configure_eva_coordinator.sh` does this over SSM and restarts the
server. It touches only `/etc/omnigent/eva.json` and the one
`OMNIGENT_EVA_CONFIG` line, never Iris's file or variable.

With no bindings, Eva is entirely inert: not registered, catalog empty,
readiness 404. Unbinding is therefore the rollback, and it needs no deploy.

## Rollout, in order, with the owner of each step

| # | Step | Who |
|---|---|---|
| 1 | The outreach app actually syncs the CRM test copy. `sync_runs > 0`, verified on the **direct** warehouse route, not through the gateway. | the sync session |
| 2 | Outreach app container on `i-02eb2f52439574844` beside `omnigent-server`, with its env file placed by hand. | Abram + an agent |
| 3 | DNS and a cert for `eva.airbrx.ai`. It has no DNS today. | **Abram** |
| 4 | `aws sso login --profile airbrx-prod`, then `scripts/eva/configure_eva_coordinator.sh`. | **Abram**, interactive |
| 5 | Merge this branch to `omnigent-airbrx-server`. **This deploys production**, via `.github/workflows/deploy-omnigent-airbrx.yml`, SSM to the one box. There is no staging. | **Abram's call** |
| 6 | Acceptance below. | anyone |

Steps 3 and 4 cannot be done by an agent. Step 5 should not be.

## Acceptance

```sh
TOKEN=$(python -c "from omnigent.cli_auth import load_token; print(load_token('https://omnigent.airbrx.ai'))")
curl -sS -H "Authorization: Bearer $TOKEN" https://omnigent.airbrx.ai/v1/eva
curl -sS -H "Authorization: Bearer $TOKEN" https://omnigent.airbrx.ai/v1/eva/readiness
```

- `/v1/eva` returns an `agent_id` and the caller's bindings, and **no token
  reference**. It is omitted rather than redacted: a redacted field still tells a
  reader the shape and location of the secret.
- `/v1/eva/readiness` returns `carries_crm_data: true`. If it is `null` the app
  does not report sync state and you have established nothing; if it is `false`
  the app has never synced and Eva must be unbound.
- The drawer lists Eva beside Iris. Start a chat. Ask her to list the pool. The
  leads that come back are real companies, not `@*.example`.
- Ask her to mark a draft sent. She must refuse, and the refusal must come from
  the boundary rather than from the model being agreeable.

## Status

Implemented and tested locally: 80 tests in `tests/airbrx/`, ruff clean.
Readiness verified against the live outreach app on `127.0.0.1:8000`, where it
correctly reported `healthy: true` with `carries_crm_data: null` and
`sheets: "not configured"`.

**Not done:** nothing is deployed, no binding exists on any coordinator,
`eva.airbrx.ai` has no DNS, and the outreach app has still never synced the CRM.
