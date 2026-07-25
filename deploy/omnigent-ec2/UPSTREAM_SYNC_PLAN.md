# Upstream sync plan — July 2026 (437-commit catch-up)

Execution runbook for merging `upstream/main` into the airbrx fork and
redeploying `omnigent.airbrx.ai`. This is a **migration-bearing** sync — read
the whole thing before starting.

## Where we are

| | ref | date | notes |
|---|---|---|---|
| Last sync (merge-base) | `2c4bae40` | 2026-07-13 | `fix(polly): default Cursor workers…` |
| Upstream tip | `76281b94` | 2026-07-24 | `feat(onboarding): write harness credential (M3)` |
| Our `main` tip | `ebfb8652` | 2026-07-24 | host-install + shared-hosts + admin→Settings |

- **Upstream ahead:** 437 commits (90 feat, 180 fix, 38 perf).
- **Our fork-only work:** 46 commits — OIDC admin-claim, shared/always-on hosts,
  host version/OS/visibility, admin→Settings, `omnigent host install`, deploy
  scaffolding.

## The three real collisions

Everything else fast-forwards or merges trivially. These three need hands:

### 1. Alembic DAG — two heads, one merge migration (the main risk)

Both branches fork from the shared node **`z5a2b3c4d5e6`** (`drop_partial_indexes`):

```
z5a2b3c4d5e6 ─┬─ (upstream) … → b3c4d5e6f7a8   ← upstream single head (add_config_to_projects)
              └─ (fork) abx1 → abx2 → abx3 → abx4e2f3a4b5   ← our single head (host cols)
```

After the code merge there will be **exactly two heads**. Resolve with one
`alembic merge`. Good news: the two branches touch the `hosts` table
**orthogonally** —

- **Ours ADD columns:** `hosts.version` (abx1), `hosts.os` (abx2),
  `hosts.login_token_expires_at` (abx3), `hosts.visibility` + `hosts.workroot`
  + `ck_hosts_visibility` (abx4).
- **Upstream MODIFIES existing columns/constraints:** drop `hosts.token_hash`
  unique (`f82e866d9de0`), compress host/policy text → CompressedText
  (`z9a2b3c4d5e6`), unify `user_id` columns (`b3c1a2d4e5f6`), and the global
  16-byte binary-UUID conversion (`z7a2b3c4d5e6`).

No column-name overlap → the merge migration is a **no-op join**, not a
data-rewrite. But two upstream migrations are **heavy data rewrites on a
populated Aurora DB** and set the deploy's risk profile:

- `z7a2b3c4d5e6_convert_ids_to_binary_uuid` — rewrites every id column to
  16-byte binary across large tables (`conversation_items`, etc.). Long-running.
- `aa1b2c3d4e5f_split_conversations_to_metadata` + `z8…widen_conversation_items_pk`
  — table splits / PK widening on the hottest tables.

These are why this deploy **must** take a manual Aurora snapshot and be
dry-run against a snapshot clone first (see Phase 3).

### 2. OIDC — textual conflict, complementary features

Both edit the same functions; conflict is small and reconcilable:

| file | our Δ (admin-claim) | upstream Δ (#2223 email-claim) |
|---|---|---|
| `server/oidc.py` | +32 | +25 (both additive) |
| `server/routes/auth.py` | +34 / −21 | +50 / −8 |
| `cli_auth.py` | +18 | 0 |

- **Upstream #2223** adds `OMNIGENT_OIDC_EMAIL_CLAIM` (which id_token claim
  carries the *email identity* — for Entra UPN tokens).
- **Ours** adds `OMNIGENT_OIDC_ADMIN_CLAIM` / `OMNIGENT_OIDC_ADMIN_VALUE`
  (which claim drives *admin promotion*).

Different concerns in the same resolver → keep both. When reconciling
`_resolve_oidc_identity` / `_resolve_oidc_email`, layer the email-claim lookup
first (identity), then our admin-claim evaluation (authorization). Preserve our
break-glass admin-file fallback running last.

### 3. Features we designed/built that upstream now ships

Not code conflicts — scope decisions to make **before** merging so we don't
carry redundant fork work:

- **Session import/export** (`#2649`, `#3032`, `#3046`, `#3141`) — upstream now
  has `omnigent session import`/`export` + Claude Code/Codex/Qwen/Kiro/Pi/Kimi/
  OpenCode importers. Compare against our approved `session-export-import-design`
  before building ours — upstream likely covers most of it.
- **Remote harness install from UI** (`#2912`, `#2987`, `#3088`) — conceptually
  adjacent to `omnigent host install`. Decide whether ours stays fork-only or
  we adopt upstream's flow.

## Execution

### Phase 0 — prep (no prod impact)

1. Confirm clean tree; back up refs:
   ```
   git fetch upstream --quiet && git fetch origin --quiet
   git branch backup/main-pre-upstream-sync main
   git branch backup/airbrx-server-pre-sync origin/omnigent-airbrx-server
   ```
2. Create the work branch off our `main`:
   ```
   git checkout main && git checkout -b chore/upstream-sync-2026-07
   ```

### Phase 1 — merge the code

1. `git merge upstream/main` (expect conflicts concentrated in the 3 OIDC files
   + possibly `openapi.json`, `web/src/App.tsx`, Sidebar/nav, and any host-page
   files upstream's host WS-push touched).
2. Resolve OIDC per §2 (email-claim + admin-claim compose). Regenerate
   `openapi.json` rather than hand-merging it.
3. Do **not** hand-resolve Alembic yet — resolve code first, get it importing.

### Phase 2 — resolve the migration DAG

1. Verify two heads:
   ```
   uv run alembic -c omnigent/db/alembic.ini heads   # expect b3c4d5e6f7a8 + abx4e2f3a4b5
   ```
2. Create the merge migration (empty upgrade/downgrade — pure join):
   ```
   uv run alembic -c omnigent/db/alembic.ini merge -m "merge host cols with upstream schema" \
     b3c4d5e6f7a8 abx4e2f3a4b5
   ```
3. Confirm a single head afterward: `alembic heads` → one revision.
4. **Consider** a follow-up migration to fold our `hosts.version` / `hosts.os`
   into CompressedText for parity with `z9…` (optional; not a blocker).

### Phase 3 — prove migrations on a snapshot clone (gate before prod)

This is the non-negotiable safety gate — the runbook says CD does **not**
snapshot, and this delta has heavy data rewrites.

1. Manual snapshot of the live cluster:
   ```
   aws rds create-db-cluster-snapshot --db-cluster-identifier omnigent-pg \
     --db-cluster-snapshot-identifier omnigent-pg-pre-upstream-sync-$(date -u +%Y%m%dt%H%M%Sz)
   ```
2. Restore the snapshot to a throwaway cluster (`omnigent-pg-syncdryrun`),
   point a scratch `DATABASE_URL` (psycopg3 form) at it, and run
   `alembic upgrade head`. **Time it** — the binary-UUID conversion is the long
   pole; that number is the prod migration window.
3. Smoke the app against the clone (health 200, `/v1/sessions`, `/v1/hosts`,
   admin surface, a host row). Then **delete the dry-run cluster.**

### Phase 4 — CI + tests

1. `pre-commit run --all-files`; fix.
2. `uv run pytest tests/host tests/server/test_oidc_callback.py tests/db -q`
   plus the e2e_ui admin/sidebar suites we maintain.
3. Push the branch, open a PR into airbrx `main`, let fork CI go green
   (ignore the upstream "Maintainer Approval" gate — not applicable to our fork).

### Phase 5 — land + deploy

1. Merge the sync PR to airbrx `main` (fast-forward or squash — our call; `main`
   isn't branch-protected).
2. **Take a fresh snapshot immediately before deploy** (state moved since the
   dry-run in Phase 3).
3. Merge `main` into `omnigent-airbrx-server`, push → `deploy-omnigent-airbrx.yml`
   auto-deploys over SSM. The box runs `alembic upgrade head` on boot; the
   workflow's health-check window must cover the migration time measured in
   Phase 3 — **bump the health-check timeout in the workflow if the dry-run ran
   long**, or the deploy will red-flag a healthy-but-still-migrating box.
4. Verify: health 200, `DEPLOYED_HEAD` matches, admin surface + OIDC login
   (incognito, fresh `/auth/callback`) + a host row + new upstream surfaces
   (Projects sidebar, Scheduled Tasks) all load.

## Rollback

- **Pre-merge:** `git reset --hard backup/main-pre-upstream-sync`.
- **Deploy failed / migration wedged:** restore the Phase-5 snapshot to a new
  cluster, repoint `DATABASE_URL` in `/etc/omnigent/server.env`, restart; or
  `git reset --hard backup/airbrx-server-pre-sync` on the box + restart to the
  prior code. Aurora snapshot restore is the source of truth for data.

## Risk table

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Binary-UUID migration runs long, health-check times out | Med | Med | Phase 3 timing → bump workflow timeout |
| OIDC merge drops admin-claim or email-claim | Med | High (lockout) | §2 compose; break-glass admin file; incognito verify |
| Alembic multi-head not caught → boot fails | Low | High | Phase 2 `heads` check + Phase 3 dry-run |
| Data-rewrite migration corrupts hot tables | Low | Critical | Snapshot + clone dry-run before prod |
| Host-page/web conflicts from WS-push refactor | Med | Low | Resolve in Phase 1; e2e_ui suite |

## Decisions (resolved)

1. **Session import/export** — DECIDED: adopt upstream's (`omnigent session
   export`/`import`), retire our own build, AND add one fork feature on top:
   `omnigent session compact` — offline recovery for wedged, un-compactable
   sessions. See "Fork add-on: external compaction" below.
2. **Remote harness install** — DECIDED: keep `omnigent host install` fork-only
   (it bootstraps the host *daemon*; upstream has no equivalent). Accept
   upstream's harness-CLI-install feature in the merge but leave it **dormant**
   — `OMNIGENT_HARNESS_INSTALL_ENABLED` unset (route 404s). Revisit enabling
   once we want members self-installing harness CLIs; before flipping it,
   confirm our host image has `npm` (upstream's installer runs `npm i -g`).
3. **Sync cadence** — DECIDED: single 437-commit catch-up on the work branch,
   gated hard by the Phase-3 snapshot-clone dry-run. (Not staged/soaked.)

## Fork add-on: external compaction (`omnigent session compact`)

**Problem.** A session wedges when static tool/MCP definitions + history exceed
the model context window. In-place `/compact` can't help: `_prepare_messages`
rebuilds the prompt *including the full tool schemas*, so even the compaction
turn's input is over budget. `compact()` can strip tool-result *bodies* from the
transcript but not the tool *definitions* (those come from the spec/connectors,
not the transcript).

**Why it's clean to build.** Two upstream facts make this small:
- The export format (`cli.py`, JSONL `{session_meta, items[]}`) contains **only
  conversation items — never tool/MCP definitions**. The bloat that wedges the
  session doesn't exist in the export.
- `runtime/compaction.py:548 compact()` takes `messages`/`history`/`model`/
  budget as plain params and already summarizes with an **empty tool surface**
  (`tools=[]`). It needs no live session.

**Design.** A third subcommand in the existing `session` group:
`omnigent session compact -i <export.jsonl> -o <compacted.jsonl> --model <m>
[--recent-window N]`. Wiring: reuse `session_import`'s JSONL reader →
rehydrate items (`entities/conversation.py parse_item_data`) → build the
messages list → call `compact(..., system_token_budget=0, runner_client=None,
force=True)` (or `llms/summarize.py` directly, as `runner/app.py:/v1/summarize`
does) → emit a leaner JSONL (`session_meta` + one `compaction` item + preserved
recent window) → `omnigent session import` it as a fresh history-only session.
Fully offline; runs against any model with headroom. Net-new fork code, no
upstream conflict — build it **after** the sync lands.

Key files: `cli.py` (session group), `runtime/compaction.py:548`,
`llms/summarize.py`, `entities/conversation.py` (`CompactionData:407`,
`parse_item_data`), `runner/app.py` (`/v1/summarize` reference impl).
