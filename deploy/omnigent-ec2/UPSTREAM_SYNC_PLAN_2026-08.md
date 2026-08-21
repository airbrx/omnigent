# Upstream sync plan — August 2026 (874-commit catch-up)

Execution runbook for merging `upstream/main` into the airbrx fork and
redeploying `omnigent.airbrx.ai`. This is a **migration-bearing** sync — read
the whole thing before starting. Supersedes `UPSTREAM_SYNC_PLAN.md` (the July
437-commit sync, now historical); the process is identical, only bigger.

## Where we are

| | ref | date | notes |
|---|---|---|---|
| Last sync (merge-base) | `4788a77d` | 2026-07-25 | `test: stabilize two known flakes (#3224)` |
| Upstream tip | `1f575ba8` | 2026-08-19 | current `upstream/main` |
| Our `main` tip | `23802e75` | 2026-08-05 | post-July-sync + /clear-relaunch fixes + Fable + shared-hosts |
| Our deploy tip | `27d0c048` | 2026-08-06 | `omnigent-airbrx-server`, currently live |

- **Upstream ahead:** 874 commits since the merge-base (84 fix(web), 64 refactor,
  30 feat(web), 25 fix(runner), 16 fix(claude-native), …). 1,827 files,
  +261,869 / −73,387.
- **Our fork-only work:** 82 commits — OIDC admin-claim, shared/always-on hosts,
  host version/OS/visibility, admin→Settings, `omnigent host install`, Fable
  restore, the /clear+relaunch native fixes (prefix-tolerant guard, executor
  bridge-dir, host-bind rollback, composer waiting-status), deploy scaffolding +
  auto-deploy CI.

## Rule (do not skip)

Land the sync on **`main` first** (via PR), then merge `main` →
`omnigent-airbrx-server` and push (the push fires the deploy Action, which
auto-upgrades the connected host runners). Never merge upstream straight onto
the deploy branch. (See memory: deploy-branch-flows-through-main.)

## The real collisions

Everything else fast-forwards or merges trivially. These need hands:

### 1. Alembic DAG — two heads, one merge migration (the main risk, but lighter than July)

Both branches fork from the last shared node and each carry a single head:
- **Ours (fork):** the `abx*` host-column chain → merge node
  `6287525878c2_merge_host_cols_with_upstream_schema` (added in the July sync).
- **Upstream:** its own head after the new migrations below.

After the code merge there will be **exactly two heads** → resolve with one
`alembic merge -m "merge upstream <sha> into airbrx"`.

**Good news this round — the hosts table did NOT change upstream.** Our
`hosts.version/os/login_token_expires_at/visibility/workroot` columns stay
fork-private with zero new collision (verified: no upstream migration touches
`hosts`). So the merge migration is again a **no-op join**, not a data rewrite.

**New upstream migrations to apply** (17 files touched; the net new ones):
- `d5e6f7a8b9c0_rename_projects_owner_user_id` + `e6f7a8b9c0d1_compress_projects_config`
  — projects table rework (rename `owner_user_id`→`user_id`, drop name UNIQUE,
  compress config). Data-touching but small table.
- `d5e9f1a2b3c4_conversation_search_trgm_indexes` — **pg_trgm trigram indexes
  for session content search.** Built with `CREATE INDEX CONCURRENTLY` (upstream
  fix `6384aac5`), so it runs **outside a transaction** — confirm alembic's
  `op` uses autocommit / non-transactional mode on our Aurora, and expect it to
  take a while on `conversation_items`. This is a **feature win**: in-app search
  was a no-op on Postgres for us; this turns it on.
- `za2b3c4d5e6f_add_task_summary_to_conversation_metadata` — additive column.
- `c4d5e6f7a8b9_add_session_approval_delegation` **+**
  `f7a8b9c0d1e2_drop_session_approval_delegation` — these **cancel out** (the
  approval/delegation stack was added then reverted upstream, `7efe0562`
  reverting `#2150`). Net schema effect: none. They still apply in order.
- The **`M` (modified) migrations** (`z7…binary_uuid`, `u1…enums_smallint`,
  `z9…compress_policy_host`, `z4…compress_opaque`, `b3c1…unify_user_id`,
  `b7e4…merge_agent_config`) — upstream edits are **tiny type-only refactors**
  (`d65f150e` narrow driver values, `537fa405` type compressed-text decode,
  `c4f377f0` type batch-recreation-mode; e.g. `z7` is +4/−2 lines). These
  migrations **already ran on our Aurora**, so alembic will NOT re-run them —
  the body edits only affect a fresh DB. Confirm during the dry-run; treat as
  low risk.

No `z7…convert_ids_to_binary_uuid` / `split_conversations` heavy rewrite this
round (those landed in July). **This sync has no long-running data rewrite** —
its DB risk is the trgm CONCURRENTLY index build, not a table rewrite.

### 2. Textual conflicts — expected in the fork-owned surfaces

Areas where our fork and upstream both edit the same files (reconcile, keep both):
- **OIDC** (`_resolve_oidc_identity` / auth) — our admin-claim promotion vs
  upstream auth changes. Same as July: small, complementary.
- **Admin → Settings surface + Usage — KEEP BOTH (nearly free, not a product
  call).** The two features are on different axes: our fork's Members rollup is
  **admin-scoped, cross-user** ("who on my server spends what" — sessions/hosts/
  tokens/cost columns on `MembersPage`, backed by `/v1/admin/users`), while
  upstream's `feat(web): add Usage page with session cost tracking` (#4673,
  `77eae57b`) is a **self-scoped** personal dashboard (a new top-level sidebar
  `UsagePage`: daily cost chart, breakdown by harness/model, sortable session
  table, time-range selector, backed by the non-admin `/usage` endpoint). They
  stack, they don't duplicate. 3-way check (base `4788a77d`) shows the merge is
  mostly clean-takes:
  - `admin.py` — **fork-only file, upstream never had it** → no conflict, ours
    survives wholesale (the `−411` in the 2-way diff is just "upstream lacks our
    file", not a delete).
  - `usage.py`, `web/src/shell/Sidebar.tsx` — **fork untouched since base**;
    upstream extended both → **clean take-upstream** (the Usage nav item + page
    come in for free).
  - `schemas.py` + store — upstream **additive only** (`SessionUsage` gains
    `harness`/`llm_model`/`agent_name`; new `DailyCost` + `list_daily_costs`) →
    clean take-upstream. Our admin rollup keeps reading
    `session_usage["total_cost_usd"]` / `["total_tokens"]` (upstream only adds
    fields) — re-confirm during the merge.
  - **`web/src/pages/MembersPage.tsx` — the ONE genuine hand-merge** (both sides
    rewrote it; our changes are additive rollup columns).
  Upgrade to take while in there: upstream's `_resolve_session_harness()` 3-tier
  fallback + the new harness/model fields are better cost plumbing than our
  rollup currently sits on — our per-member view could gain a harness/model
  breakdown by consuming them.
- **host_id sharding + wake-host.** Upstream `feat: shard managed-server host +
  session traffic by host_id` touches the same host/session-routing area as our
  shared-hosts feature. Reconcile carefully; our shared/always-on host semantics
  must survive. **Also reconcile Michael's fork-only `feat(hosts): let the picker
  wake an offline host with cloud compute` (`9e3d2e95`, on `main`) + its openapi
  regen (`23802e75`)** — it lives in the same host-picker / host-routing surface
  and overlaps upstream's `feat(sessions): auto-connect a wakeable runner on shell
  create`. Decide whether to keep our picker-driven wake, adopt upstream's
  auto-connect, or both. Michael's `feat(devbox): codify the omnigent dev box +
  Actions power control` (`5e0fa8b1`) is standalone infra — low conflict risk,
  just carry it forward.
- **Sidebar / projects rework** (`web/src/shell/` is the top churn area at 8.4%)
  — our nav customizations vs upstream's projects-in-hero changes.

### 3. Deploy CI + lockfile

- `uv sync --locked` must pass — our `f4bbb3d2 fix(hosts): drop the ec2 extra`
  exists for this; re-verify against the merged lockfile.
- Keep our auto-deploy workflow (`deploy-omnigent-airbrx.yml`) and the
  psycopg-reinstall-after-uv-sync step; upstream churns `.github/workflows/`
  heavily (3.6%), so expect workflow conflicts — keep OURS for the deploy file.

## What we gain (highlights of the 874)

- **Session content search that works** (pg_trgm), Usage/cost page, project
  settings editor + composer prefill, session filter menu.
- **Harness credential from the New Chat setup dialog** (`feat(web)` M3 +
  `feat(onboarding): write harness credential`) — directly relevant to the
  **AWS/Databricks/LLM credential-portability** thread; adopt and evaluate
  whether it closes that gap.
- **New policies**: destructive-op gating, force-push / tag-push protection,
  `detect_loop` / `detect_thrashing` / retry-loop guards, configurable dangerous
  shell gating.
- **Harnesses**: Devin, Grok Build (xAI), `NativeHarnessProvider` seam; claude
  status-file idle/working derivation; `claude-opus-5` in the curated catalog.
- **Infra**: shard host/session traffic by `host_id`, route host-scoped requests
  to the replica holding the tunnel, copy-on-write zygote forkserver,
  `omnigent start` / `host --background`.

## Execution (phased)

**Phase 0 — branch + backups.** Cut `sync/upstream-2026-08` off `origin/main`.
Take a manual Aurora snapshot (`aws rds create-db-cluster-snapshot
--db-cluster-identifier omnigent-pg --db-cluster-snapshot-identifier
omnigent-pg-pre-sync-2026-08-<ts>`) — the deploy Action does NOT snapshot.

**Phase 1 — code merge.** `git merge upstream/main` on the sync branch. Resolve
conflicts in the four surfaces above (OIDC, admin/Usage, host sharding vs
shared-hosts, sidebar/projects). Keep our deploy workflow + lockfile fix.

**Phase 2 — alembic.** Confirm two heads (`alembic heads`); create one
`alembic merge`. Read the trgm-index migration for CONCURRENTLY/transaction
handling. `alembic upgrade head` locally against a **snapshot clone**, not
Aurora.

**Phase 3 — dry-run on a snapshot clone.** Restore the Phase-0 snapshot to a
throwaway instance, point a local server at it, run `alembic upgrade head`, and
smoke-test. Confirm the `M` migrations don't re-run and the trgm build completes.

**Phase 4 — CI green on the PR to `main`.** Push the sync branch, open a PR to
`main`, get all Pytest/E2E shards green (upstream churns tests heavily; expect
fixups). Merge to `main`.

**Phase 5 — deploy.** Merge `main` → `omnigent-airbrx-server`, push. The Action
builds the SPA, SSMs the box (`git reset` → `uv sync` → reinstall psycopg →
`pull-webui` → restamp `_build_info.py` → restart → migrate → health-check).
Connected host runners auto-upgrade on the new HEAD. Watch the run; verify
`omnigent.airbrx.ai` health + a live native session end-to-end.

**Rollback:** restore the pre-sync snapshot + `git reset --hard` the box to the
prior deploy sha (`27d0c048`) and re-run `pull-webui` for the matching SPA.
