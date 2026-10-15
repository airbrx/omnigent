# Upstream sync plan — October 2026 (2,132-commit catch-up)

Execution runbook for merging `upstream/main` into the airbrx fork and
redeploying `omnigent.airbrx.ai`. This is a **migration-bearing** sync — read
the whole thing before starting. Supersedes `UPSTREAM_SYNC_PLAN_2026-08.md`
(the August 901-commit sync, now historical).

## Where we are

| | ref | date | notes |
|---|---|---|---|
| Last sync (merge-base) | `32f0d78e` | 2026-08-20 | `fix(tools): paginate agent and session discovery (#4892)` |
| Upstream tip | `f4f81681c` | 2026-10-15 | current `upstream/main` (v0.16.0) |
| Our `main` tip | `fd1cc5a58` | 2026-10-15 | post-Aug-sync + pnpm deploy pipeline |
| Our deploy tip | same | 2026-10-15 | `omnigent-airbrx-server`, currently live |
| Our alembic head | `f1b087457345` | — | merge of `6287525878c2` + `za2b3c4d5e6f` |

- **Upstream ahead:** 2,132 commits, 255 `feat(` commits. Roughly double the August sync.
- **Our fork-only work since last sync:** 4 native-session fixes (bridge-dir,
  prefix-tolerant guard, atomic host-bind rollback, composer waiting-status);
  pnpm deploy pipeline; Bridge dir fix #2 in `orchestration.py`.

## Rule (do not skip)

Land the sync on **`main` first** (via PR), then merge `main` →
`omnigent-airbrx-server` and push. Never merge upstream straight onto the
deploy branch. (See memory: deploy-branch-flows-through-main.)

## The real collisions

### 1. `orchestration.py` — the heaviest reconcile

Upstream rewrote `omnigent/runner/native/orchestration.py` extensively:
**+3,189/−572** lines. Our fork's only change is Fix #2 from July 2026
(~12 lines in `_auto_create_claude_terminal` — bridge-dir honors label
verbatim instead of overriding to `session_id`).

Strategy: take upstream wholesale, then reapply our 12-line fix manually.
The fix is small and localized (one function body). Verify the test
`test_auto_create_claude_terminal_honours_inherited_bridge_id_label` still
passes after reconcile.

### 2. Alembic DAG — multi-head join

After the code merge, new upstream migrations chain off `za2b3c4d5e6f`
(already in our repo from Aug sync). Our fork head `f1b087457345` also
points to `za2b3c4d5e6f`. Result: multiple heads that need a merge migration.

**New upstream migration chain (17 truly new files):**
```
za2b3c4d5e6f  ← already in our repo (Aug sync merged it)
  └─ za1b2c3d4e5f  add_scheduled_tasks_max_cost_usd
       └─ e5d9bc8ac650  add_scheduled_tasks_permission_mode
            └─ ga1b2c3d4e5f  add_connections  ← NEW TABLE, see §3
                 └─ gb1b2c3d4e5f  add_terminating_sandbox_id (hosts)
                      └─ gc1b2c3d4e5f  add_hosts_deleted_at  ← touches hosts
                           └─ gd..ge..gf..gg1b2c3d4e5f  bgnd titles + trgm drop + reaper index
                                └─ gh → hh2c → hh1b2c3d4e5f  blob_key_to_files [HEAD 1]
                                     └─ ar1b2c3d4e5f  account_revocation
                                          └─ hi → ii → jj → kk → ll → mm1a2b3c4d5e [HEAD 2]
```
Plus independently: `r1a2b3c4d5e6  add_workspace_id_to_all_tables`
(chains off `q1a2b3c4d5e6`, already in our repo).

**After merge there will be 3 heads:**
- `f1b087457345` (our fork)
- `hh1b2c3d4e5f` (upstream chain A, if `ar1b2c3d4e5f` not in the set)
- `mm1a2b3c4d5e` (upstream chain B)

Resolve with one `alembic merge -m "merge upstream f4f81681c into airbrx fork"`.
The merge migration is again a **no-op join**.

**Migration notes by file:**

- `gc1b2c3d4e5f_add_hosts_deleted_at` — adds `deleted_at INTEGER` to `hosts`.
  Our fork adds `version/os/login_token_expires_at/visibility` columns.
  No column name collision; both use `batch_alter_table`. **No conflict** —
  just verify `alembic upgrade head` applies our abx columns before this one
  (they chain independently through `f1b087457345`).

- `r1a2b3c4d5e6_add_workspace_id_to_all_tables` — adds `workspace_id` column
  + widens PKs on 12 tables including `hosts`. Our fork's `abx*` migrations
  add columns to `hosts` but don't touch the PK. **No direct conflict** —
  but verify the `batch_alter_table(recreate="always")` on SQLite doesn't
  drop our extra columns. (Shouldn't; batch recreate rebuilds from reflected
  schema which includes our columns.)

- `ga1b2c3d4e5f_add_connections` — entirely new table, no conflict. This is
  the provider-agnostic credential store (see §3 below).

- `gd..ge` (`add_background_session_titles_user_setting` then
  `drop_background_session_titles_user_setting`) — these cancel out, still
  apply in order.

- `r1a2b3c4d5e6` already has a prerequisite `q1a2b3c4d5e6` which exists in
  our repo. Confirm `alembic heads` doesn't include `r1a2b3c4d5e6` as a
  separate branch (it should chain cleanly from `q1a2b3c4d5e6`).

**No long-running data rewrite this round** — the `add_workspace_id` migration
uses `server_default=0` for existing rows and `batch_alter_table` for PK
widening. Expect it to take a few seconds on our small Aurora, not minutes.

### 3. Credential store — adopt and evaluate

`feat(server): provider-agnostic per-user credential store` (#4514):
- New `connections` table: one encrypted secret blob per
  `(workspace_id, user_id, provider, account_id)`.
- Secrets encrypted at rest with AWS KMS (IRSA, never in DB plaintext).
- First consumer: GitHub App; later: MCP connectors + sandbox credential broker.

This is **directly relevant to the AWS/Databricks/LLM cred-portability
problem** Ben raised. Evaluate whether it closes the gap or whether our
planned persistent per-session tool-permission policy is still needed
separately. No action required during merge — just take it as-is.

### 4. GitHub workflows

Upstream churns `.github/workflows/` heavily. **Keep our
`deploy-omnigent-airbrx.yml`** (the SSM deploy Action) and our
`setup-pnpm` composite action. Take upstream's other workflow changes.

### 5. Other textual conflict surfaces

Same surfaces as August, now 2× larger:

- **OIDC** — our admin-claim promotion vs upstream auth changes; small,
  complementary, keep both.
- **Admin/Settings + Usage** — already reconciled in August; re-verify the
  merge didn't revert our `MembersPage.tsx` admin-rollup columns.
- **Sidebar/shell** — upstream added Inbox, session grouping, drag-reorder.
  Our nav customizations survive; just check for import conflicts.
- **`web/src/hooks/useHosts.ts`** — keep Michael's `useWakeHost` + upstream's
  new harness/installer hooks (same strategy as August).
- **`web/src/App.tsx`** — keep our `LegacyRedirect` + upstream's new routes.

## What we gain (highlights)

- **Per-user credential store** — AWS KMS-encrypted, provider-agnostic.
  Direct answer to the cred-portability thread.
- **Agent install from GitHub** (`omnigent agent add`) + custom agent
  management settings UI.
- **Session sidebar**: grouping by date/created/updated, stable sort,
  drag-reorder pinned sessions, Inbox with unread filter tabs.
- **`web_read` builtin** — fetch a page's content as clean markdown (new
  agent tool, no config needed).
- **Sandbox**: per-harness providers + curated model selection, Git SSH
  broker, `Support refreshing sandbox credential sources`.
- **iOS**: OIDC sign-in through system browser, Databricks OAuth persistence.
- **Policies**: `fix(policies): require approval for scheduled task changes`.
- **Host daemon**: `feat(host): report host daemon crashes and exits to the
  debug-log sink`.

## Execution (phased)

**Phase 0 — branch + backups.** Cut `sync/upstream-2026-10` off
`origin/main`. Take a manual Aurora snapshot:
```
aws rds create-db-cluster-snapshot \
  --db-cluster-identifier omnigent-pg \
  --db-cluster-snapshot-identifier omnigent-pg-pre-sync-2026-10-<ts>
```
(The deploy Action does NOT snapshot automatically.)

**Phase 1 — code merge.** `git merge upstream/main --no-ff` on the sync
branch. Resolve conflicts; key decisions:

1. `orchestration.py`: take upstream, then reapply Fix #2 (12 lines in
   `_auto_create_claude_terminal`). Run its test to confirm.
2. `deploy-omnigent-airbrx.yml`: keep ours. Discard upstream's conflicting
   workflow changes.
3. Admin/Usage/sidebar surfaces: take upstream where upstream is ahead; keep
   our fork additions.
4. `web/src/hooks/useHosts.ts`: keep `useWakeHost` + union of all fields.

**Phase 2 — alembic.** After the merge, run `alembic heads`. Expect 3 heads
(`f1b087457345`, `hh1b2c3d4e5f` or `mm1a2b3c4d5e`, and possibly a third).
Create one merge migration:
```
alembic merge -m "merge upstream f4f81681c into airbrx fork" <head1> <head2> ...
```
Verify the merge migration is a no-op (pass/pass body).

**Phase 3 — dry-run on a snapshot clone.** Restore the Phase-0 snapshot to
a throwaway RDS instance. Point a local server at it, run
`alembic upgrade head`, smoke-test. Confirm the `workspace_id` PK widening
completes and our `abx*` columns survive. Teardown throwaway.

**Phase 4 — CI green on the PR to `main`.** Push sync branch, open PR to
`main`. Expect fixups in the same areas as August (E2E shard agent-file
discovery, ruff-format sweep, pyrefly type errors from new upstream types).
Get all shards green, merge to `main`.

**Phase 5 — deploy.** Merge `main` → `omnigent-airbrx-server`, push. The
Action: git reset → uv sync → reinstall psycopg → pull-webui → restart →
migrate → health-check. Connected host runners auto-upgrade. Watch the run;
verify `omnigent.airbrx.ai` health + a live native session end-to-end.

**Rollback:** restore the pre-sync snapshot + `git reset --hard` the box to
the prior deploy sha and re-run `pull-webui` for the matching SPA.
