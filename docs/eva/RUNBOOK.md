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

1. The bundle's `tools.outreach.tools` allow list: 15 of the original 17 tools
   in `contracts/mcp_tools.md`, plus the four LinkedIn and GTM plan tools
   (`list_linkedin_posts`, `record_linkedin_metrics`, `list_plan_items`,
   `record_plan_actual`) added on 2026-09-26, plus `set_linkedin_post_url`
   (airbrx-outreach#149, 2026-09-27), which fills an empty post address with
   one post's `https://www.linkedin.com/` address and nothing else.
2. `omnigent.airbrx.eva.policy.tool_boundary`, registered as a `tool_call`
   guardrail, fail-closed.
3. The outreach app's own tool layer, which is the authoritative one.

Three tools are withheld by name, and all are product refusals rather than
permission settings:

- **`approve_draft`**: approval belongs to the lead owner or an admin and never
  to the draft's author. Eva writes drafts.
- **`mark_sent`**: nothing in this system sends. A rep sends from their own
  client and then records that they did. An agent marking a draft sent would be
  recording an event it cannot observe.
- **`upsert_linkedin_post`**: it rewrites a post's title, status, dates or
  copy. Eva only fills an empty address, with `set_linkedin_post_url`. The
  server lets any rep upsert, so for Eva this refusal is layers 1 and 2 only.

Falsified rather than asserted: neutering `tool_boundary` to always ALLOW turns
red exactly the four denial cases and nothing else.

## A browser for LinkedIn stats (2026-09-26)

Abram approved Eva using a browser to read LinkedIn post analytics herself.
"Refresh stats with Eva" on the LinkedIn page now has her find each posted
item without an address on its author's recent-activity page
(`https://www.linkedin.com/in/<handle>/recent-activity/all/`, matched by date
and opening words), record its address with `set_linkedin_post_url`, then open
its analytics on linkedin.com and record the numbers with `record_linkedin_metrics`,
once LinkedIn has been signed in on her profile. Until then, and whenever a call
is refused, she says so and falls back to the paste-in route.

**Why a declared server and not Claude in Chrome.** The fence below only holds
for tools Omnigent dispatches. Claude in Chrome's tools would come from the CLI
itself (never, in fact: the claude-sdk executor does not pass `--chrome`), and a
measured Iris turn ran a CLI-loaded tool (`ToolSearch`) with no policy
evaluation. It would also drive the operator's own Chrome, signed in as them
everywhere. So the browser is Playwright MCP, declared in Eva's bundle as a
stdio server the runner spawns on the execution host. Its tools reach her CLI
through the one `omnigent` server as `browser__<tool>`, so `--strict-mcp-config`
(#97) and the `Skill` removal (#98) are unchanged.

**The fences, outermost first.**

1. The bundle's `tools.browser.tools` allow list: `browser_navigate`,
   `browser_snapshot`, `browser_wait_for`, `browser_navigate_back`,
   `browser_close`. No click, type, key, form, evaluate, run code, upload,
   screenshot, tab, cookie or network tool, so she cannot post, comment, react
   or message whatever a page says.
2. `tool_boundary`: a navigate must start with `https://www.linkedin.com/` and
   carry no whitespace, control character or backslash; each tool may carry
   only its named arguments (`browser_snapshot` never `filename`, which writes a
   file); a wait is at most 30 seconds; a bare or differently namespaced browser
   name is refused. The engine sends `data` as `{"name", "arguments"}`, the
   inner stack as `{"tool", "args"}`; both are read, and unreadable arguments
   are a denial.
3. `--allowed-origins "https://www.linkedin.com;*.licdn.com"`: the browser
   requests nothing else. Playwright documents this as not a security boundary
   and not covering redirects, which is why 2 exists.
4. `--no-webmcp`: a page cannot register tools of its own.

**What is installed on the execution host (LOCAL ONLY, as the operator, no
sudo).**

- `~/.eva-playwright`: `npm install --save-exact @playwright/mcp@0.0.82` from
  registry.npmjs.org (Microsoft's package, repo microsoft/playwright-mcp; it
  brings playwright and playwright-core 1.64.0-alpha-1789764292000). Nothing
  global.
- The Chrome channel is the installed Google Chrome
  (`/Applications/Google Chrome.app`), which `--browser chrome` launches. No
  Playwright browser download was needed.
- `~/.eva-linkedin-profile` (mode 700): Eva's own browser profile, used by
  nothing else. Headless during her turns, with Chrome's sandbox on
  (`--sandbox`; Playwright turns it off by default).
- `~/.eva-playwright/output`: where Playwright writes the snapshot a navigate
  takes; capped at 20 MB. Eva reads pages with an explicit `browser_snapshot`,
  which returns the text inline.

The bundle launches it through `/bin/sh -c` only so `$HOME` resolves on the
host that runs her turn: spec `args` are not environment-expanded, and a path
naming one operator's home would be wrong on any other host. A host without
the install fails to start the browser server and Eva falls back to paste-in.

**The one-time sign-in (the operator, by hand).** Playwright launches Chrome
with `--use-mock-keychain`, so a profile signed in by an ordinary Chrome window
cannot be read by Eva's browser. The sign-in has to happen in a
Playwright-launched window on the same profile:

```sh
~/.eva-playwright/sign-in.sh
```

It opens a headed Chrome on `~/.eva-linkedin-profile` at
`https://www.linkedin.com/login` and returns when that window is closed. Sign
in as the account whose post analytics Eva should read, then close the window.
Nobody else types or sees the credentials. Run it while no Eva turn is using the
browser (one Chrome at a time per profile). Re-run it whenever Eva reports that
LinkedIn wants a sign-in.

**Known limits.** One profile means one browser at a time: two Eva sessions
opening the browser at once on the same host, the second one fails to launch
and falls back to paste-in. On a coordinator that has no install (production
EC2), anything that resolves her MCP schemas server-side sees the browser as a
failed server; her turns run on the execution host, where it is installed.

**Measured on the local 6770 stack, 2026-09-26, profile not yet signed in.**
"Refresh stats with Eva" from the LinkedIn tab: her CLI `init` listed the one
`omnigent` server with exactly the five `browser__` tools beside her outreach
tools (strict MCP config held); she read the posts, navigated to
`https://www.linkedin.com/in/me/recent-activity/all/`, landed on the sign-up
wall, closed the browser and said LinkedIn needs a sign-in, recorded nothing
and offered the paste-in route. No click, type or sign-in attempt. The first
run found that the workspace's own turn check rejected any `browser__` call
and interrupted her; fixed in the same PR.

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

## The outreach app inside Omnigent (`/eva/app`)

Since 2026-09-26 the outreach app runs on the same box as the Omnigent server
and is served inside it at `https://omnigent.airbrx.ai/eva/app`, with the rep's
identity signed by Omnigent. The contract and why are in
[WORKSPACE.md](WORKSPACE.md) ("Identity contract v1"). Two settings, both
environment variables on the coordinator (`/etc/omnigent/server.env`), never
files:

| Variable | Meaning | Default |
|---|---|---|
| `OUTREACH_IDENTITY_SECRET` | The HMAC key shared with the outreach app. At least 32 bytes. The **same value** goes in the outreach app's environment. | unset: `/eva/app` answers 503 and forwards nothing |
| `OUTREACH_UPSTREAM` | The outreach app's origin as the coordinator reaches it. An origin only, no path. | `http://127.0.0.1:8000` |

Generate the secret once, on the box, and put it in both environments without
it passing through a terminal log or a file in a repository:

```sh
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Then restart the Omnigent server. The secret is read per request, but only a
restarted process sees a changed environment. Rotating it means changing both
sides and restarting both; in between, every `/eva/app` request is refused by
the outreach app (401), which is the correct failure.

Checks, in order:

- Signed out: `curl -sS -o /dev/null -w '%{http_code}' https://omnigent.airbrx.ai/eva/app/`
  answers `401`, from Omnigent, and the outreach app's log shows nothing.
- Signed in as a rep, in a browser: `/eva/app/` renders the app without its
  own top nav. As a signed-in Airbrx user who is not an active rep: `403`,
  from the outreach app.
- `503` means the secret is unset or shorter than 32 bytes on the coordinator,
  or `OUTREACH_UPSTREAM` is not an origin. `502` means nothing answered at
  the upstream: `curl -sS http://127.0.0.1:8000/readyz` on the box.
- `https://omnigent.airbrx.ai/eva/app/mcp` is always `404`. Eva's MCP calls go
  to `http://127.0.0.1:8000/mcp` directly with the rep's own token, never
  through Omnigent.

**Never mark an Omnigent host shared.** On a shared host a relaunched session
resolves launch providers for the host owner, so one rep's session could
receive another rep's outreach MCP token. Per-rep tokens and single-owner
hosts stay as they are.

## The token bridge, and why it is a bridge

Nothing yet turns the binding's `token_ref` into the `OUTREACH_MCP_TOKEN` the
runner expands. The reference is parsed, validated, stored and reported, and
until 2026-09-24 nothing resolved it, which is why every attempt to run Eva
ended in a 401 on a literal `${OUTREACH_MCP_TOKEN}`.
`omnigent/airbrx/eva/runtime.py` now does the resolution; it is not yet wired
into a runner launch.

Tonight's bridge on the Air, which is what makes the production binding work:
a second host process with its own identity (`OMNIGENT_HOST_ID` /
`OMNIGENT_HOST_NAME`, `OMNIGENT_DATA_DIR` holding a copy of
`auth_tokens.json`), started by a wrapper that reads
`keychain:eva-outreach-token` at start, exports
`OUTREACH_MCP_URL=http://127.0.0.1:8000/mcp/` and `OUTREACH_MCP_TOKEN`, sets
`OMNIGENT_RUNNER_ENV_PASSTHROUGH=OUTREACH_MCP_URL,OUTREACH_MCP_TOKEN`, and
execs `omnigent host --server https://omnigent.airbrx.ai`. No file and no plist
holds the token.

**This is a bridge, not the design.** A host-wide variable is right for one
developer on one Mac and wrong for the hosted product, where each rep has their
own token and a runner must receive only its own session's. The durable form is
`host/connect.py` resolving the binding per session at runner launch, which is
safe for the reason that file already states: there is one live runner per
session.

The bridge runs as a user LaunchAgent on the Air, `ai.omnigent.eva-host`
(`~/Library/LaunchAgents/ai.omnigent.eva-host.plist`, `KeepAlive`,
`RunAtLoad`). It runs `~/.omnigent-eva-host/eva_host.sh`, which reads
`keychain:eva-outreach-token` at start and execs `omnigent host --server
https://omnigent.airbrx.ai --non-interactive --auto-upgrade` under the identity
in `~/.omnigent-eva-host/host_id` (`f501802f3f0c4d22a8b1c64763cef4e1`). Neither
the plist nor the script holds a secret; the plist's environment is `HOME`,
`USER`, `LC_CTYPE` and `PATH` and nothing else, and the keychain read works
from launchd without a prompt because the same python binary stored the entry.

Rotate the token by storing a new value under the same name, then
`launchctl kickstart -k gui/$UID/ai.omnigent.eva-host`. Stop it with
`launchctl bootout gui/$UID/ai.omnigent.eva-host`. Its log is
`~/.omnigent-eva-host/data/logs/launchd.log`.

**A LaunchAgent lives in the user's GUI domain.** It survives a Claude session
ending, and it stops at logout, and it does not start after a reboot until
Abram logs in. So the bridge is up whenever he is logged in, which is not the
same as always. Always would be a LaunchDaemon, a different set of tradeoffs,
and his call.

**And while the Mac sleeps, Eva has no host.** The bridge reconnects on wake
and needs no attention, but a session started during a sleep finds no runner.
Observed on 2026-09-24: the Air slept for 85 seconds mid-test. Combined with
the GUI-domain limit, the honest statement is that Eva is available while Abram
is logged in and his laptop is awake, which is a laptop's availability and not
a service's. Moving the app and the bridge to the mini, or to the EC2 box, is
what changes that.

**The first session after a bridge restart can fail once.** The first zygote
fork on a cold host took 28 seconds and the coordinator gave up first, with
"host did not respond to launch request". The retry succeeded. So a single
failure immediately after a kickstart is expected rather than diagnostic; try
again before looking for a cause.

**The outreach app the bridge talks to** runs on the same Mac as the user
LaunchAgent `ai.airbrx.outreach-app`
(`~/Library/LaunchAgents/ai.airbrx.outreach-app.plist`, `KeepAlive`,
`RunAtLoad`). It runs `~/.omnigent-eva-host/outreach_app.sh`, which starts
uvicorn on `127.0.0.1:8000` from a clone of `main` at `~/.airbrx-outreach-app`
with its own virtualenv. Update it with `git pull --ff-only && uv sync --frozen`
in that clone, then `launchctl kickstart -k gui/$UID/ai.airbrx.outreach-app`.
Its log is `~/.airbrx-outreach-app-logs/launchd.log`.

Its environment is the canonical runtime env file at
`~/.airbrx-outreach-app/.env` (`0600`), and the old path under `~/Documents` is
a symlink to it, because a launchd agent cannot read `~/Documents`. Edit either
path, then kickstart.

It was briefly a copy rather than a symlink, and that was worth correcting
quickly: editing the file a person knows about while the app reads a different
one leaves the app on the old values with no error anywhere to say so. That is
the same shape as the MCP token silently replaced three times in one day, and
worse, because there is no 401 to announce it. One file cannot drift.

Like the bridge, this agent lives in the GUI domain: up while Abram is logged
in, not after a reboot until he logs in.

Without the app, Eva has a host and no tools, which presents as calls that fail
rather than an agent that is missing. That is the more confusing of the two
failures, so check `curl 127.0.0.1:8000/readyz` before reaching for the token.

## Rollout, in order, with the owner of each step

| # | Step | Who |
|---|---|---|
| 1 | The outreach app holds real CRM rows and no fixtures: `leads.total > 0` and `leads.fixture == 0` on its `/readyz`, verified on the **direct** warehouse route, not through the gateway. An import counts; `sync_runs` is reported beside it and is a separate fact. | the sync session |
| 2 | The outreach app running on the execution host named by `host_id`, at `http://127.0.0.1:8000`, with that host online against the coordinator. No container on `i-02eb2f52439574844` and no DNS: neither is needed, and `eva.airbrx.ai` is not planned. | Abram |
| 3 | **Pull `main` first**, then `aws sso login --profile airbrx-prod`, then `scripts/eva/configure_eva_coordinator.sh`. The binding shape changed on 2026-09-22 and a stale copy wrote a binding the deployed code refused, which crash-looped the coordinator. | **Abram**, interactive |
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
- Ask her to mark a draft sent. On the shipped bundle the tool is not offered,
  so the record shows her searching for it and finding nothing; that is the
  allow list, not the policy. The policy denial is only observable when the
  tool is offered, and it arrives as the **tool result**, not as a stream
  event: in `GET /v1/sessions/{id}/items` a `function_call` item for
  `outreach__mark_sent` followed by a `function_call_output` whose output is
  `{"error": "Denied by policy: Eva may not call mark_sent: ..."}`. A refusal
  written in prose with no `function_call` item is the model being agreeable
  and is not evidence.

## Verified locally, 2026-09-23

Not a deployment. One Omnigent server on this Mac (port 6769, private sqlite),
Eva registered from this bundle, one `omnigent host` against it with
`OMNIGENT_RUNNER_ENV_PASSTHROUGH=OUTREACH_MCP_URL,OUTREACH_MCP_TOKEN`, and the
outreach app on `127.0.0.1:8000` holding 115 real leads and no fixtures. Each
turn was a session created by `POST /v1/sessions`, a runner launched on the host
by `POST /v1/hosts/{host_id}/runners`, and the record read back from
`GET /v1/sessions/{id}/items`.

- **The tools attach and the data is real.** Eva called `outreach__list_pool`
  once and answered "115 leads total", naming the first five companies in the
  pool. They were real prospects, so they are not repeated here: this
  repository is public. No `@*.example` anywhere.
- **The allow list removes the two tools before the model sees them.** Asked to
  call `mark_sent`, Eva searched for it twice and got "No matching deferred
  tools found". That is the first refusal doing its job; it is not a policy
  denial and it is not written up as one.
- **The policy denies a call that is actually attempted.** A scratch copy of
  the bundle that offered `mark_sent` and `approve_draft` (deleted after the
  run, never committed) produced, for each, a `function_call` item with
  `draft_id 00000000-0000-0000-0000-000000000000` and the result
  `{"error": "Denied by policy: Eva may not call mark_sent: nothing in this
  system sends; a rep records their own send"}`, and the equivalent for
  `approve_draft`. The outreach app's log shows neither tool name: the runner's
  guardrail stopped the call before it left the machine. `drafts`, `touches`,
  `agent_runs` and `claims` were 0 on the direct warehouse route before and
  after.

Two airbrx-outreach bugs had to be fixed first, both on its PR #72: the MCP
tool layer bound the Lakebase facade regardless of `OUTREACH_BACKEND`, and the
warehouse facade used the caller object's repr as a rep id. No MCP tool call had
ever worked on the warehouse backend before that.

## Status

Implemented and tested locally: 80 tests in `tests/airbrx/`, ruff clean.
Readiness verified against the live outreach app on `127.0.0.1:8000`, where it
correctly reported `healthy: true` with `carries_crm_data: null` and
`sheets: "not configured"`.

**Bound on production 2026-09-24 00:46Z** to the Air's bridge host. A
production `list_pool` returned 115 real leads with no fixtures, in session
`b3ea0e63bcd646f68d1c6c1b7dd3e45b`, and again through the launchd bridge in
`dc1e70f46a974cc4a88a2f178a95cdc5` after the session-bound process was stopped,
and again in `e498122ffb334cea99fb67d8e39369e4` with both the bridge and the app
running as LaunchAgents. The CRM sync itself has still never run,
and that is reported as its own field rather than folded into the readiness
answer.

**Two coordinator outages the same night**, each a few minutes, each fixed by
the rollback above. The first: a stale copy of the binding script wrote a
binding with no `host_id`, which the deployed code refuses. The second:
registration expanded the bundle at startup and the coordinator has no
`OUTREACH_MCP_TOKEN`, by design. Both crash-looped the server rather than
disabling one agent, which PR #68 changes.

**The placeholders are gone, as of 2026-09-24.** They were load bearing for a
few hours and that history is worth keeping. The coordinator expanded the
bundle in more than one place. PR #68 fixed registration, creating a session
still expanded, and with the placeholders removed the server started cleanly
and returned 400 on every new Eva chat, which is a worse failure than the crash
loop because the service looks healthy. PR #75 fixed it at the root: a bundle
declares `env_expansion: runner`, the coordinator loads it unexpanded, and the
runner resolves the variables as it always did. Only `OMNIGENT_EVA_CONFIG`
remains in `/etc/omnigent/server.env`.

Proven with a session rather than a health check: `42f0497ed53b423f8b6b88e01bf87221`
created without a 400 and answered `list_pool` with 115 real leads.

**Do not put a real token there to make expansion succeed.** That is still the
fix somebody reaches for, and it would hand every session of that agent, on
every host, the same credential.
