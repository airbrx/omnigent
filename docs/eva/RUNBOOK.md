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
She still binds to an execution host, and for the same reason Iris does: her
MCP client runs on the runner, the runner runs on the bound host, and that host
can reach an outreach app on its own loopback. The coordinator cannot. So:

| Iris | Eva |
|---|---|
| vendored `iris-source.zip` + `source.json` digest | nothing; two tracked files read where they lie |
| `host_id` in the binding | `host_id` in the binding, the same mechanism |
| `pat_ref` resolved from a machine keychain | `token_ref` resolved on the host at the point of use |
| `scripts/iris/{vendor,store_pat,prepare_host,verify_host}.py` | none |
| a launchd plist delta on the Mac mini | none |

`tests/airbrx/test_eva_bundle.py::test_every_tool_is_remote_so_no_execution_host_is_needed`
pins the first row. If `local_tools` ever stops being empty, the vendoring and
verification apparatus comes back with it and somebody should have to argue for
it first.

**The bound host has to be awake.** Eva's turns run there, and the outreach app
she talks to runs there too. An earlier version of this document said the
opposite and was wrong: it assumed the coordinator would dial the app, which
needs a public hostname that does not exist and is not planned.

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
    "base_url": "http://127.0.0.1:8000",
    "host_id": "882128953d2a4e178ddbd48d70b298a1",
    "token_ref": "keychain:eva-outreach-token",
    "fixture": false
  }
]
```

This is the shape `scripts/eva/configure_eva_coordinator.sh` writes. Unknown
keys are refused rather than ignored, so a typo in an authorization file stops
startup instead of silently widening or narrowing who can open the agent. A
binding without a `host_id` is refused for the same reason: without one Eva has
no machine to run on and no way to reach the app.

`base_url` is the outreach app **as the execution host sees it**. The loopback
address means the app running on that Mac, which is the normal case. The
coordinator never dials it, which is why `/v1/eva/readiness` answers `null` for
such a binding and says where to check instead (below).

Set `OMNIGENT_EVA_CONFIG` on the coordinator in `/etc/omnigent/server.env`.
`scripts/eva/configure_eva_coordinator.sh` does this over SSM and restarts the
server. It touches only `/etc/omnigent/eva.json` and the one
`OMNIGENT_EVA_CONFIG` line, never Iris's file or variable.

With no bindings, Eva is entirely inert: not registered, catalog empty,
readiness 404. Unbinding is therefore the rollback, and it needs no deploy.

## Rollout, in order, with the owner of each step

| # | Step | Who |
|---|---|---|
| 1 | The outreach app holds real CRM rows and no fixtures: `leads.total > 0` and `leads.fixture == 0` on its `/readyz`, verified on the **direct** warehouse route, not through the gateway. An import counts; `sync_runs` is reported beside it and is a separate fact. | the sync session |
| 2 | The outreach app running on the execution host named by `host_id`, at `http://127.0.0.1:8000`, with that host online against the coordinator. No container on `i-02eb2f52439574844` and no DNS: neither is needed, and `eva.airbrx.ai` is not planned. | Abram |
| 3 | `aws sso login --profile airbrx-prod`, then `scripts/eva/configure_eva_coordinator.sh`. | **Abram**, interactive |
| 4 | Merge to `omnigent-airbrx-server`. **This deploys production**, via `.github/workflows/deploy-omnigent-airbrx.yml`, SSM to the one box. There is no staging. | **Abram's call** |
| 5 | Acceptance below. | anyone |

Step 3 cannot be done by an agent. Step 4 should not be.

## Acceptance

```sh
TOKEN=$(python -c "from omnigent.cli_auth import load_token; print(load_token('https://omnigent.airbrx.ai'))")
curl -sS -H "Authorization: Bearer $TOKEN" https://omnigent.airbrx.ai/v1/eva
curl -sS -H "Authorization: Bearer $TOKEN" https://omnigent.airbrx.ai/v1/eva/readiness
```

- `/v1/eva` returns an `agent_id` and the caller's bindings, and **no token
  reference**. It is omitted rather than redacted: a redacted field still tells a
  reader the shape and location of the secret.
- `/v1/eva/readiness` answers `null` for a loopback binding, with a `detail`
  saying so, because the coordinator cannot dial the execution host. Run the
  check on that host instead:

  ```sh
  curl -sS http://127.0.0.1:8000/readyz | python3 -m json.tool
  ```

  `leads.total` must be above zero and `leads.fixture` must be zero. `false`
  from `/v1/eva/readiness`, or a non-zero `fixture` count on the host, means Eva
  is in front of invented or missing data and must be unbound. `sync.runs` is
  reported beside the counts and is not the same fact: an import is not a sync.
- The drawer lists Eva beside Iris. Start a chat. Ask her to list the pool. The
  leads that come back are real companies, not `@*.example`.
- Ask her to mark a draft sent. She must refuse, and the refusal must come from
  the boundary rather than from the model being agreeable.

## Status

Implemented and tested locally: 80 tests in `tests/airbrx/`, ruff clean.
Readiness verified against the live outreach app on `127.0.0.1:8000`, where it
correctly reported `healthy: true` with `carries_crm_data: null` and
`sheets: "not configured"`.

**Not done:** no binding exists on any coordinator, so Eva is inert in
production. The outreach app on Abram's Mac holds 115 real leads and no
fixtures; the CRM sync itself has still never run, and that is reported as its
own field rather than folded into the readiness answer.
