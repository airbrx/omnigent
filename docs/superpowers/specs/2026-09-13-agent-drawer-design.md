# Agent drawer — design

**Date:** 2026-09-13
**Status:** approved, not yet implemented
**Baseline:** the airbrx fork at upstream v0.13.0 (`sync/upstream-v0.12` → v0.13 merge)

## Problem

Agents are chosen from a `<select>` buried in the new-chat landing screen. As
the team installs more of them, that stops scaling: you cannot see the roster
at a glance, and there is nothing to distinguish one agent from another beyond
a name in a dropdown. We want a slide-out drawer that lists every agent as a
recognisable teammate — picture, name, description — reachable from anywhere.

## Decisions taken

| Question | Decision | Why |
|---|---|---|
| Where it lives | Core UI patch | v0.13 extensions contribute **pages and primary-nav entries only** — there is no contribution slot for chrome, so a real slide-out drawer cannot be an extension. |
| What it lists | The existing registered agents | No new backend concept; the drawer is a better view of what `GET /v1/agents` already returns. |
| Where pictures live | Fork-local `agent_avatars` table + the existing artifact store | `AgentSpec` is a **versioned** bundle format the SDK also reads; forking it is the most expensive divergence we could take on. |

## Architecture

### UI

`web/src/shell/AgentDrawer.tsx`, built on the same primitive as the existing
`FilesPanelDrawer` / `MobilePanelDrawer`. Opened from a rail button in the
sidebar; slides over the current view.

Selecting an agent starts a new session with it via **the same create path the
landing picker already uses**, so drawer and picker cannot drift.

The landing picker **stays**. `NewChatDialog.tsx` conflicted in both stages of
the v0.11 → v0.13 sync; a new sibling component plus one mount point is a far
smaller permanent diff than rewiring the landing screen.

### Data

New fork-local table `agent_avatars`:

| Column | Notes |
|---|---|
| `workspace_id` | matches the workspace partitioning v0.12 added to every table |
| `agent_name` | |
| `artifact_key` | key into the existing artifact store |
| `content_type` | validated on upload |
| `updated_at` | drives cache validation on the image route |

Primary key `(workspace_id, agent_name)`.

Image bytes go through the **existing artifact store** (`put(key, bytes)` /
`get(key)`), which already has local, S3, and Databricks-volumes backends — so
avatars need no new storage mechanism and work on every deployment shape.

One alembic revision, on our branch. The next upstream sync reconciles heads
with a no-op merge revision exactly as `09a6063c8614` and `0c8ef9677127` did.

### API

New router `omnigent/server/routes/agent_avatars.py` — a file upstream does not
have, so it can never conflict.

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/agent-avatars` | list `{agent_name, url, updated_at}` for the drawer |
| GET | `/v1/agent-avatars/{agent_name}` | image bytes, cacheable on `updated_at` |
| PUT | `/v1/agent-avatars/{agent_name}` | multipart upload; content-type + size validated |
| DELETE | `/v1/agent-avatars/{agent_name}` | remove |

With no avatar set, the UI renders deterministic coloured initials derived from
the agent name, so the drawer looks complete before anyone uploads anything.

## Fork-divergence containment

This is the design's main non-functional constraint. Carrying 89 fork commits
is what turned the v0.11 → v0.13 upgrade into 31 conflicts across two stages.

Everything new lives in files upstream does not have:

- `web/src/shell/AgentDrawer.tsx`
- `omnigent/server/routes/agent_avatars.py`
- the avatar store module
- one alembic revision

The **only** edits to upstream-owned files are the rail button + drawer mount in
the shell, and one router registration in `app.py`. That is the whole ongoing
sync cost, and it is deliberate.

## Testing

**vitest** — list renders; initials fallback with no avatar; selecting an agent
starts a session with that agent; open/close behaviour. Any full module mock of
`@/hooks/useHosts` must export `useWakeHost`: the landing screen calls it, and
three separate upstream test files broke on exactly this during the v0.13 sync.

**pytest** — avatar round-trip through the artifact store; content-type and
size rejection; 404 for an unknown agent; workspace scoping, so one workspace
cannot read another's avatar.

Regenerate `openapi.json` (CI syncs it to the docs site).

## Out of scope for this slice

- **Slice 3 — cost and usage surface.** A UI showing agent token spend, cost
  *avoided* by using subscription accounts rather than metered API access, and
  the Snowflake/Databricks cost of running Iris and other agents **via Airbrx
  versus direct**, with the agent's metadata hosted on Databricks.

  Prior art to reuse rather than rebuild: `airbrx-cost-analysis` already
  computes per-tenant cost attribution from the gateway's own request logs,
  reconciled to the Cost Explorer bill, and `signal-cost-reporter` runs it
  nightly. That covers the "via Airbrx" half. The genuinely new work is the
  warehouse-side cost and the **counterfactual** — "what this would have cost
  direct" is a modelled baseline, not a meter, and it needs a defensible
  methodology before any number is put on a screen. Design it as its own slice.

- A separate "teammate" entity distinct from agents.
