# Iris workspace v2: the contract every lane builds against

2026-09-26. Owner: Iris Chief of Staff (changes to this file go through them).
Goal (Abram): Iris's workspace at `omnigent.airbrx.ai/iris` gets Eva's shape
(chat docked on the right and collapsible to a rail, tabs, Eva's light airbrx
branding and landing, a streaming turn, chat kept on reload, markdown) without
breaking any of Iris's rules. Background and evidence:
`iris-ui-study-2026-09-26.md` (Iris CoS workspace). Eva's design:
`docs/eva/WORKSPACE.md`.

## 0. Defaults adopted, each one switch

| | Default | The switch | Who builds it |
|---|---|---|---|
| D1 | Iris's UI moves out of the pinned archive into `omnigent/airbrx/iris/ui/`. The Python package, agent bundle and `ui/assets/` (portrait, logo) stay pinned in `iris-source.zip`. | Env `OMNIGENT_IRIS_UI`, read per request in the asset route: `v2` serves the new UI plus the kernel; anything else serves the pinned UI plus `host.js`, as today. The default stays pinned until W5 flips it. | W1 (switch), W5 (flip) |
| D2 | A stale capture (older than 300 s) is shown with Eva's label, "Iris read this tenant {when}, may be out of date", and one background refresh runs. It no longer blocks on a forced collection. The first-ever open (409) still auto-collects, as today. | `<body data-on-stale="show">` in `iris/ui/index.html`. `collect` brings back today's behaviour: block, and refresh once. | W3 |
| D3 | Eva's light airbrx palette (kernel `brand.css` tokens). The theme follows the host: `?theme=` at mount, then `postMessage({irisHostTheme})`. No theme picker, no `localStorage["iris.theme"]`. | `<html data-theme-source="host">`. Anything else would be a token override block in `iris/ui/style.css`, which is not built unless asked. | W2 (tokens, theme), W3 |

## 1. Lanes and file ownership

A lane may edit only the files it owns. Anything else is a request to the owning lane through the board.

| Lane | Owns (exclusive) | Must not touch |
|---|---|---|
| **W1 adapter** | `omnigent/airbrx/iris/routes.py`, `omnigent/airbrx/iris/records.py`, a new `omnigent/airbrx/iris/workspace.py` if the state builder moves out, `tests/airbrx/test_iris_routes.py`, new `tests/airbrx/test_iris_workspace_fixtures.py`, generated `web/src/shell/__fixtures__/irisAdapter.json`. **Also `pyproject.toml` package-data and `openapi.json`** (the only lane that touches either). | The `account` handler in `routes.py` (see 6), `account.py`, `config.py`, the pinned archive |
| **W2 kernel** | New `omnigent/airbrx/workspace/__init__.py`, `omnigent/airbrx/workspace/assets.py` (the `KERNEL_ASSETS` allowlist and `kernel_asset(name)`), `omnigent/airbrx/workspace/ui/**`, new `web/src/shell/workspaceKernel.test.ts` | `omnigent/airbrx/eva/**` (Eva's files are copied from, never edited) |
| **W3 Iris app** | New `omnigent/airbrx/iris/ui/` (`index.html`, `app.js`, `style.css`, `views/*.js` if split), new `web/src/shell/irisWorkspaceApp.test.ts` | Kernel files (ask W2), `host.js` |
| **W4 shell** | `web/src/shell/IrisWorkspace.tsx`, `IrisAccountView.tsx` (only where the landing or Resume needs it), `IrisWorkspace.test.tsx`, `IrisAccountView.test.tsx` | `tests/airbrx/test_iris_account_invariants.py`, `EvaWorkspace.tsx`, `App.tsx` routes |
| **W5 cutover** (last) | `omnigent/airbrx/iris/host.js`, `web/src/shell/irisHostAdapter.test.tsx`, `scripts/iris/vendor.py`, `source.json` / `iris-source.zip`, `docs/iris/**` (except this file); in airbrx/iris: `ui/README.md`, user guide, `omnigent/AGENTS.md` | Everything above, until W1 to W4 are merged |

Package-data (W1): add `"ui/**/*"` to `"omnigent.airbrx.iris"`, and a new
`"omnigent.airbrx.workspace" = ["ui/**/*"]`. `openapi.json` (W1): the planned
changes add no schema routes (the asset route is `include_in_schema=False`,
and state is an untyped dict). If `tests/server/test_openapi_drift.py` fails
anyway, W1 regenerates with `scripts/dump_openapi.py`, and no one else does.

## 2. Adapter state contract (W1 serves, W3 reads)

Routes are unchanged: `/v1/iris/sessions/{id}/ui/api/{chat,cancel,state,refresh,readiness}`.
Shapes stay Iris's (the ones Eva copied): chat takes `{history, deadline}` and
answers `{text, tools, failed, item_id}`; `state` is 409 until an overview
exists; `refresh` runs one turn and answers with the state body, or 409 if the
turn produced no overview.

**`GET .../state` and `POST .../refresh` body.** Examples are from the
fixture tenant `fixture-iris`, live capture 2026-09-27T00:21Z.

| Key | Type | Status | Example / meaning | Read by |
|---|---|---|---|---|
| `overview` | iris_overview report | existing | whole report | Overview, Evidence |
| `overview.metrics` | object | existing | `{requests: 700, cache_hits: 560, cache_misses: 140, hit_rate: 0.8, hit_rate_denominator: 700, start_date: "2026-09-19", end_date: "2026-09-26", covered_days: 7, requested_days: 7, period_complete: true, latency: {response_time_ms: null, label}}` | Overview KPIs, coverage |
| `overview.evidence[]` | array | existing | `{id: "8e884fb5959440d8a32e7f823425ee1c", source_tool: "get_cache_opportunities", start_date, end_date, status: "complete", data, limitations}`; `get_summary` rows (one per day) are the coverage strip | Overview, Evidence |
| `overview.findings[]`, `overview.limitations[]`, `overview.mode`, `overview.tenant_id`, `overview.generatedAt` | | existing | `mode: "synthetic fixture"` drives the synthetic chip | Overview, header |
| `audit.findings[]` | array (`{findings: []}` when absent) | existing | `{id: "unknown_sensitivity:report-cache", kind: "unknown_sensitivity", severity: "warning", confidence: "low", rule_id: "report-cache", explanation, next_step, evidence_ids: ["32151338598b4a26a2aed9d75860ae6a"]}` | Findings, Rules |
| `rules[]` | array | existing | `{ruleId: "report-cache", cacheHits: 80, cacheMisses: 20, totalExecutions: 100}` (+ `ruleName, queriesMatched, hitRate, warehouseTimeSavedMs` when the tool sends them) | Rules |
| `rule_effectiveness_meta` | object | existing | `{year, generatedAt, totalQueries}`, all `null` in the fixture. An annual window, labelled apart from the period | Rules |
| `stale` | bool | existing, unchanged | `cache_age_seconds > 300` | header, D2 |
| `cache_age_seconds` | int | existing | `28` | header |
| `monitoring` | null | existing | always `null`: "Monitoring is unavailable" | Overview |
| **`captured_at`** | float, epoch seconds | **new (W1)** | `created_at` of the item that carried the overview report. The clock `cache_age_seconds` is measured on | header freshness |
| **`investigation`** | iris_investigate report or `null` | **new (W1)** | `{current: <metrics>, previous: <metrics>, findings, evidence, limitations, message, mode, tenant_id}` | Evidence, Results |
| **`proposal`** | iris_propose report or `null` | **new (W1)** | `{proposal_status: "validated", proposal_view: {rule_id: "report-cache", baseline_hash: "f21cb3ff…", diff: "--- baseline\n+++ candidate…ttlSeconds 60 → 120", validation: {valid, errors, warnings}, preview: {written: false, …}}, message: "Validated proposal. Proposal not applied. …"}` (shape from `iris:docs/examples/proposal/report.json`) | Proposals |
| **`report_times`** | object | **new (W1)** | `{iris_overview: 1790..., iris_audit: 1790..., iris_investigate: null, iris_propose: null}`, epoch seconds per tool, `null` when absent | every tab's "as of" line |

W1 rules for the new keys:
- Same tenant check as today: any report whose `tenant_id` is not the binding's gets a 403.
- `investigation` and `proposal` are the newest of their tool in the session. The refresh marker does **not** bound them, because a refresh never calls those tools and would otherwise blank the Proposals tab. Their own `report_times` entry says how old each is. `overview` and `audit` keep today's refresh bound.
- The refresh prompt keeps its first sentence byte-for-byte, `Call iris_overview and iris_audit for the selected tenant.`, because W3's history reload recognises it.

**Evidence ids.** `findings[].evidence_ids` resolve against the union of `evidence[]` across `overview`, `audit`, `investigation` and `proposal`, keyed by `id`. No new key is needed.

**`GET .../readiness`** is unchanged:
`{tenant_id, name, fixture, session_status, turn_completed_here, last_task_failed, verified[], unverified[]}`.
It never forwards host error text.

**Assets under `OMNIGENT_IRIS_UI=v2`** (W1). The route is `/v1/iris/sessions/{id}/ui/{asset}`, after `authorize()`:

| Asset | Served from | Headers |
|---|---|---|
| `index.html` | `omnigent/airbrx/iris/ui/` | `no-store`, `X-Frame-Options: SAMEORIGIN`, no host.js injection |
| `app.js`, `style.css`, `views/*.js` | `omnigent/airbrx/iris/ui/` | `no-store` |
| `kernel/<name>` | W2's `kernel_asset(name)`, allowlist `KERNEL_ASSETS` | `no-store` |
| `assets/iris-portrait.png`, `assets/airbrx-logo.png` | pinned `source_root()/ui/assets` | `private, max-age=3600` |

Everything else is 404, including `iris-state.json`, `demo-state.json` and `host.js`.

## 3. Kernel API (W2 provides, W3 imports)

The kernel is plain browser scripts. There is no bundler and there are no ES
modules. Each file attaches to `window.AirbrxWorkspace` (`AW` below) and is
loaded by `<script src="kernel/<file>">` before `app.js`. Tests load them from
disk into jsdom, as `evaWorkspaceApp.test.ts` loads Eva's app. Code is copied
from `omnigent/airbrx/eva/ui/app.js` and made generic: no Eva strings, no
"outreach", and the agent name is always a parameter. DOM is built with nodes
and `textContent` only; `innerHTML` is never used.

| File | API |
|---|---|
| `dom.js` | `AW.el(tag, attrs, ...children)`, `AW.$(id)` |
| `theme.js` | `AW.theme.init({messageKey})`. Applies `?theme=light\|dark` (else `prefers-color-scheme`), sets `document.documentElement.dataset.theme`, then follows `message` events with `event.origin === location.origin` carrying `{[messageKey]: "light"\|"dark"}`. Iris passes `messageKey: "irisHostTheme"`. |
| `api.js` | `AW.createApi({base: "api/", timeoutMs: 330000}) -> api(path, body?)`. GET without a body, POST JSON with one. It throws `Error` with `.status` (0 = network) and `.detail` (the FastAPI `detail`). `AW.plainError(error, {agent}) -> string`: 401, 5xx and network get fixed wording, 504 means "took too long", and only a 4xx `detail` is shown verbatim (written for people). 5xx text is **never** shown. |
| `markdown.js` | `AW.markdown(text) -> DocumentFragment`, `AW.inline(text) -> Node[]`. Bold, italic, code, bullet and numbered lists; headings render as bold paragraphs. No links, images or HTML. Eva's `INLINE` regex, verbatim. |
| `transcript.js` | `AW.createTranscript({list, agentName}) -> {add(kind, text, retry?) -> li, setText(li, text), progress(label \| null)}`. `kind` is one of `"user" \| "agent" \| "system" \| "error"`. `agent` renders markdown; the others render plain text. `progress` keeps one `role=status` line at the bottom. |
| `stream.js` | `AW.createStream({sessionId, pollMs = 1500, doing, recognise, stripUser, transcript}) -> {sessionItems(limit), markExisting(), watchTurn(shown: Set<string>) -> stop(): Promise, loadHistory() -> Promise<boolean>}`. Polls `GET /v1/sessions/{id}/items?order=desc&limit=N` same-origin. `loadHistory` reads 200 items and adds "Earlier messages are in native chat." when `has_more`. |
| `dock.js` | `AW.createDock({chat, rail, collapseButton, storageKey}) -> {collapsed(), setCollapsed(bool), render({docked})}`. Stores `"1"`/`"0"` in `localStorage[storageKey]` inside try/catch. `body.collapsed` gives the 44 px rail. Iris uses `storageKey: "iris.dockCollapsed"`. |
| `tabs.js` | `AW.createTabs({nav, tabs, onShow(tab, prev)}) -> {current(), show(id, {fromHash}), restart(id)}`. The tab lives in `#hash` via `replaceState`. Clicking the active tab calls `restart`. |
| `context.js` | `AW.cleanName(raw, max = 120)` gives one line with no control characters. `AW.createContextToggle({badge, label, button}) -> {included(), reset(), render(label)}` is the "Don't include this page" control. |
| `brand.css` | airbrx tokens on `:root` (accent `#fd6c1d`, page `#f0efed`, radii 12/16/20, Inter + IBM Plex Mono). `:root[data-theme=dark]` flips neutrals only. Layout classes: `.topbar .brand #tabs .workspace .dock .dock-rail .chat #messages #composer`. Grid `minmax(0,1fr) 380px`, 320 px under 1100 px, one column under 760 px. |

**How a tab is declared** (W3, one array, the only place tabs are listed):

```js
const TABS = [
  { id: "overview", label: "Overview", render: views.overview, home: true },
  { id: "findings", label: "Findings", render: views.findings },
  // render(container, state, ctx) draws with AW.el; ctx = {ask, readiness}
];
```

`home: true` means the chat is always shown there (Eva's Chat tab). Every other
tab has the dock beside it, collapsible to the rail. Iris has no framed pages,
so `path` is not used.

**What the stream may put in the chat.** This is a whitelist, and the kernel
test enforces it:

| Session item | Rendered as |
|---|---|
| `message`, role `assistant` | `agent`, text = its `output_text` parts joined. It grows in place while live. |
| `message`, role `user`, history only | `recognise(text)` returns a `system` line, otherwise `user` with `stripUser(text)` |
| `function_call`, live only | `progress` with label `doing[bareName] \|\| "Working"`. The bare name is `name.split("__").pop()`. **Never the tool name and never its arguments.** |
| `function_call_output`, `reasoning`, anything else | nothing, ever. They carry tenant evidence. |

Iris's `doing` (W3): `iris_overview` "Reading the tenant's traffic",
`iris_audit` "Checking the rule configuration", `iris_investigate` "Comparing
periods", `iris_propose` "Drafting and validating a rule change",
`ToolSearch` "Getting her tools ready". `recognise`: text starting with the
refresh sentence becomes "You had Iris collect a fresh overview."

## 4. Iris's tabs

| Tab | Shows | State keys | Asks (chat messages, never local answers) |
|---|---|---|---|
| **Overview** (`home`) | KPIs (hit rate with its denominator, requests, misses, response-time proxy with its label), coverage strip, the top 2 findings, period, tenant, "Monitoring is unavailable" | `overview.metrics`, `overview.evidence[get_summary]`, `overview.findings`, `monitoring`, `captured_at`, `stale` | "Explain this capture's performance" |
| **Findings** | Every finding with severity, confidence, rule and next step; review notes (local); handoff export | `audit.findings` + `overview.findings`, deduplicated by `id` | "Investigate finding {id}" |
| **Rules** | Rule-effectiveness table (annual window, labelled); configuration findings per rule | `rules`, `rule_effectiveness_meta`, `audit.findings[rule_id]` | "Propose a change to rule {ruleId}" |
| **Proposals** | Newest proposal: rule, status, validation, preview, diff, baseline hash. Always "Not applied. Needs external approval." No apply button. | `proposal`, `report_times.iris_propose` | "Revise the proposal: …" |
| **Evidence** | Evidence rows by id (the target of every `evidence_ids` link); investigation current vs previous; cache opportunities; session downloads (`report.json`, `report.md`, `proposal.json` from `/v1/sessions/{id}/resources/files`) | `overview/audit/investigation/proposal.evidence`, `investigation.current/previous` | "Dig into evidence {id}" |
| **Results** | Baseline vs now, same tenant and complete periods only (`comparisonProblem()` ported). The baseline is pinned locally: `localStorage["iris.baseline.<tenant_id>"]` = `{tenant_id, captured_at, metrics}` | `overview.metrics`, `captured_at`, `investigation` | "What changed?" |
| **Accounts** | Triage summary rows only, the columns of `IrisAccountView`, read-only | `GET /v1/iris/account` + `GET /v1/iris` (same-origin GETs from the frame) | none |

**Accounts opens the tenant in its own session and never switches tenant in
this one.** The frame never calls `POST /v1/sessions`. It posts to the shell:

```js
window.parent.postMessage({ type: "iris.openTenant", tenant_id }, location.origin);
```

The shell (W4) accepts the message only when all of these hold:
`event.origin === window.location.origin`,
`event.source === frame.current?.contentWindow`,
`event.data.type === "iris.openTenant"`, `tenant_id` is a string, and the
tenant is in `data.bindings`. Then the handoff **resumes the tenant's existing
session** via `resumableSession` (a recent session on the same `host_id` **and**
`workspace`, the landing's Resume match), read fresh from the caller's recent
sessions at that moment, and navigates there. It calls the **existing**
`create(tenant_id)` only when there is none. A failed recent-sessions read, or
one that has not answered within about 10 s (`HANDOFF_READ_TIMEOUT_MS`), counts
as none. If the resumable session is the current one, the shell stays put.
Only one open runs at a time.
The row for the current session's tenant shows "This session" and has no button;
the others show "Open".

## 5. Honesty rules: each one becomes a test

These are ported from `host.js` and `irisHostAdapter.test.tsx`. W3 owns them
in `irisWorkspaceApp.test.ts`, replayed against `irisAdapter.json`, unless
another owner is named.

1. **No synthetic data.** The app never requests `iris-state.json` or `demo-state.json`, and with state 409 it shows "Iris has not collected an overview here yet", never numbers. (W3. W1 also asserts both 404 under v2.)
2. **A refused turn is not answered in Iris's voice.** For chat failing with 409, 502, 504 or a network error, the transcript gets an `error` line: "Iris did not answer. {plainError}. Nothing has been computed in her place." No `agent` line is added.
3. **No answer from local data when the host refused.** With state loaded and chat refused, no metric, finding or rule value from state appears in the new line. `ask()` has no local path.
4. **Host error text is never shown.** A 500 whose `detail` holds a sentinel leaves the sentinel out of the DOM. (W2 unit-tests `plainError`; W3 tests it end to end.)
5. **Synthetic chip.** When `readiness.fixture` or `overview.mode === "synthetic fixture"`, a "Synthetic fixture" chip is in the header on every tab.
6. **Auto-collect once per load, on the plain read only.** A 409 on the first `state` read triggers exactly one `POST api/refresh`. A failed refresh does not loop (count = 1 after re-reads). The refresh never triggers another.
7. **Stale (D2).** With `data-on-stale="show"`, a stale 200 renders the capture with "may be out of date" plus one background refresh. With `collect`, it renders nothing until the refresh returns. Both are tested.
8. **Say what is happening while collecting.** "first overview" and "refreshing an out-of-date capture" are different sentences.
9. **330 s client deadline.** Chat and refresh use `timeoutMs: 330000`, and chat sends `deadline: 300`.
10. **The tenant must match.** A state whose `overview.tenant_id` differs from `readiness.tenant_id` is refused and not rendered. Results refuses a cross-tenant baseline.
11. **Readiness is not a hopeful blank.** When `turn_completed_here` is false, the unverified line shows. A readiness failure shows "This host will not run turns in this session", never an empty bar.
12. **Refresh keeps the chat.** No `resetConversation()`, and the transcript survives refresh and reload (`loadHistory`).
13. **Cancel** calls `api/cancel`, stops `watchTurn` and clears progress.
14. **Native chat link** `/c/{id}` is always present. The downloads list says "no files", not "no reports".
15. **No tool outputs in the chat.** A `function_call_output` and `function_call.arguments` holding a sentinel never reach the DOM. (W2 kernel test on generic items; W3 with Iris items carrying `evidence` data.)
16. **Proposals are never presented as applied.** The label is present and there is no apply control.
17. **No em dashes** (U+2014) in `iris/ui/**` or `workspace/ui/**`. (W3 and W2, each for their own files.)

## 6. Invariants that must survive

`tests/airbrx/test_iris_account_invariants.py` stays **unchanged** and green
in every PR. It parses source text, so a refactor breaks it even when behaviour
is the same. The exact strings it needs:

- `IrisWorkspace.tsx` contains `onOpen={(tenantId) => void create(tenantId)}`.
- `IrisWorkspace.tsx` has `async function create(`, and its body (up to the first `"\n  }\n"`, so keep it declared at two-space indent) calls only `authenticatedFetch("/v1/sessions"` with `method: "POST"`, and contains neither `/events` nor `/ui/api`. Resume's recent-sessions query and the `iris.openTenant` listener live **outside** `create`.
- `IrisAccountView.tsx` contains `onOpen(entry.tenant_id)`, and (case-insensitive, comments included) none of `fetch(` (which also catches `authenticatedFetch(`), `usequery`, `usemutation`, `xmlhttprequest`, `eventsource`, `websocket`.
- `IrisAccountView.tsx` has `export interface IrisRankedRow {` with exactly the required fields `tenant_id, name, hit_rate, hit_rate_denominator, requests, cache_misses, covered_days, requested_days, captured_at, age_seconds`, and `export interface IrisQuarantinedRow {` with required `tenant_id, name, reason, detail` and optional `covered_days?, requested_days?, captured_at?`.
- `routes.py` has exactly one `def account`, decorated `@router.get("/iris/account")`, with one return `{generated_at, tenants, **rank(...)}`, one tenant-row comprehension `{tenant_id, name, note}`, and a `session_client` used for `.get` only. W1 does not edit this handler, and no new function in `routes.py` may be named `account`.

Also:
- Drill-in stays one `POST /v1/sessions {agent_id, host_id, workspace}` from the shell's `create()`. **Resume** (W4) is a navigate only. It matches a recent session on `host_id` **and** `workspace`, because both production tenants share host `448499c7…` and matching on host alone opens the wrong tenant.
- The account surface returns summary rows only. No model runs on `/v1/iris/account`. Quarantine is by coverage (`period_complete`), never by age.
- A session never accumulates tenants (iris spec invariant 3).
- Tool boundary: `runtime.TOOLS` dispatch, `completed_answer` refuses any tool outside `TOOLS | {ToolSearch}`, and `validate_spec`. None of these change.
- Framing: same-origin iframe, `X-Frame-Options: SAMEORIGIN`, and `authorize()` (`permission_level >= 2` plus the binding) on every route. W4 may add `allow-popups` to the sandbox, as Eva has it.

## 7. Merge order and the gate

1. Each lane opens its own PR to `main`, with the files in its row only, and posts it to the board. Iris CoS reviews and merges. Lanes do not merge their own PRs.
2. W1, W2 and W4 run in parallel. W3 starts on hand-written fixtures and a stubbed `AW`, and merges after W2. It moves to W1's `irisAdapter.json` once W1 merges.
3. Merging to `main` deploys nothing: production deploys on pushes to `omnigent-airbrx-server`. W1 keeps `OMNIGENT_IRIS_UI` defaulting to the pinned UI, so even a deploy leaves Iris as she is today.
4. **Gate:** no push to the deploy branch until W3 and W4 are merged **and** a local QA pass is done (both tenants, `OMNIGENT_IRIS_UI=v2`, light and dark, a reload mid-chat, a refused turn, a stale capture, Accounts resuming the tenant's existing session and creating one only when there is none). Then production runs behind the switch for live QA.
5. **W5 last:** flip the default, stop serving and injecting `host.js`, delete it and its test, update `docs/iris/`, and mark `iris:ui/` dev-only. The vendor.py allowlist change (stop shipping the pinned `ui/*.html/js/css`) needs a merge commit on iris main and a re-vendor.
6. Later, and separate: Eva moves onto the kernel (W6), guarded by `evaWorkspaceApp.test.ts`.

Open for Iris CoS: whether v2 keeps the old "Import report" path. It is not in
this contract, because an imported report fills the view without a model turn.
