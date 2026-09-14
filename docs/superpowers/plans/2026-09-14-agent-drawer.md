# Agent Drawer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A slide-out drawer listing every registered agent as a recognisable teammate — picture, name, description — from which you can start a session.

**Architecture:** A new `AgentDrawer.tsx` in the shell, built on the same primitive as the existing `FilesPanelDrawer`, triggered from the left sidebar. Avatars are a fork-local `agent_avatars` table whose bytes live in the existing artifact store, served by a new `/v1/agent-avatars` router. No change to `AgentSpec`, no change to `GET /v1/agents`.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (`Mapped`/`mapped_column`) + Alembic; React 18 + TypeScript + Tailwind + TanStack Query; pytest + vitest.

**Spec:** `docs/superpowers/specs/2026-09-13-agent-drawer-design.md`

## Global Constraints

- **Never modify `AgentSpec`** (`omnigent/spec/types.py`). It is a versioned bundle format the SDK also reads. Avatars are fork-local.
- **Minimise edits to upstream-owned files.** Exactly four are touched: `omnigent/server/app.py` (one `create_app` param + one `include_router`), `omnigent/cli.py` (construct the store beside the others), and `web/src/shell/Sidebar.tsx` + `web/src/shell/AppShell.tsx` (trigger + mount). Everything else is new files upstream does not have. `NewChatDialog.tsx` is not touched at all.
- **Web HTTP goes through `authenticatedFetch` from `@/lib/identity`** — the helper every sibling hook uses (see `web/src/hooks/useHosts.ts:2`). There is no `@/lib/api`.
- **`AvailableAgent`** (`web/src/hooks/useAvailableAgents.ts:12`) is `{ id, name, display_name, description: string | null, harness, skills, ... }`. Render `display_name || name` as the label; key avatars on `name`, which is what the server stores and what `onSelectAgent` returns.
- **All new DB tables carry `workspace_id`** as the first primary-key column, defaulting to `current_workspace_id`, matching every table since upstream v0.12.
- **Migrations use `op.batch_alter_table` / `op.create_table`** and must run on SQLite (tests) and PostgreSQL (production Aurora 16.9).
- **Any vitest file that mocks `@/hooks/useHosts` must export `useWakeHost`** — the landing screen calls it, and three upstream test files broke on exactly this during the v0.13 sync.
- **Regenerate `openapi.json`** (`python scripts/dump_openapi.py`) in the task that adds routes. CI syncs it to the docs site.
- Python style: `ruff check` + `ruff format` clean. Web: `pnpm --filter web lint` clean.

---

### Task 1: `agent_avatars` table + migration

**Files:**
- Modify: `omnigent/db/db_models.py` (add `SqlAgentAvatar` after `SqlHost`)
- Create: `omnigent/db/migrations/versions/abx5a1b2c3d4_add_agent_avatars.py`
- Test: `tests/db/test_migration_agent_avatars.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SqlAgentAvatar` with columns `workspace_id: int`, `agent_name: str`, `artifact_key: str`, `content_type: str`, `updated_at: int`; PK `(workspace_id, agent_name)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_migration_agent_avatars.py
"""The agent_avatars table exists after a full migration run."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from tests.db.test_migration_connections import _build_alembic_config, _upgrade


def test_agent_avatars_table_after_full_migration(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'avatars.db'}"
    engine = sa.create_engine(uri)
    _upgrade(uri, engine, "head")
    cols = {c["name"] for c in sa.inspect(engine).get_columns("agent_avatars")}
    assert cols == {
        "workspace_id",
        "agent_name",
        "artifact_key",
        "content_type",
        "updated_at",
    }
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/python -m pytest tests/db/test_migration_agent_avatars.py -v`
Expected: FAIL — `NoSuchTableError: agent_avatars`

- [ ] **Step 3: Add the model**

Append to `omnigent/db/db_models.py`, after the `SqlHost` class:

```python
class SqlAgentAvatar(OmnigentBase):
    """
    SQLAlchemy model for the ``agent_avatars`` table.

    airbrx-local. Maps an agent NAME (not id) to an image held in the
    artifact store, so the drawer can render agents as recognisable
    teammates. Deliberately keyed on name and kept out of ``AgentSpec``:
    that is a versioned bundle format the SDK also reads, and forking it
    would be the most expensive divergence available to this fork.

    :param workspace_id: Owning workspace, matching every other table.
    :param agent_name: The agent's ``name`` as returned by ``GET /v1/agents``.
    :param artifact_key: Key into the artifact store holding the bytes.
    :param content_type: Validated image media type, e.g. ``"image/png"``.
    :param updated_at: Unix epoch seconds; drives HTTP cache validation.
    """

    __tablename__ = "agent_avatars"

    workspace_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        default=current_workspace_id,
    )
    agent_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    artifact_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)
```

- [ ] **Step 4: Add the migration**

Create `omnigent/db/migrations/versions/abx5a1b2c3d4_add_agent_avatars.py`. Set `down_revision` to the CURRENT head — find it with `.venv/bin/python -m alembic -c omnigent/db/alembic.ini heads` and substitute below:

```python
"""add agent_avatars table

Revision ID: abx5a1b2c3d4
Revises: <CURRENT_HEAD>
Create Date: 2026-09-14 00:00:00.000000

airbrx-local table backing agent avatars in the drawer. Image bytes live
in the artifact store; this table holds only the mapping and the metadata
the image route needs for cache validation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "abx5a1b2c3d4"
down_revision: str | None = "<CURRENT_HEAD>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``agent_avatars``."""
    op.create_table(
        "agent_avatars",
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(length=255), nullable=False),
        sa.Column("artifact_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "agent_name"),
    )


def downgrade() -> None:
    """Drop ``agent_avatars``."""
    op.drop_table("agent_avatars")
```

- [ ] **Step 5: Confirm the test passes and there is still ONE alembic head**

Run: `.venv/bin/python -m pytest tests/db/test_migration_agent_avatars.py tests/db/test_migration_connections.py -v`
Expected: PASS, including `test_single_alembic_head`.

- [ ] **Step 6: Commit**

```bash
git add omnigent/db/db_models.py omnigent/db/migrations/versions/abx5a1b2c3d4_add_agent_avatars.py tests/db/test_migration_agent_avatars.py
git commit -m "feat(db): agent_avatars table for fork-local agent pictures"
```

---

### Task 2: Avatar store

**Files:**
- Create: `omnigent/stores/agent_avatar_store.py`
- Test: `tests/stores/test_agent_avatar_store.py`

**Interfaces:**
- Consumes: `SqlAgentAvatar` (Task 1); `ArtifactStore.put/get/delete/exists`.
- Produces: `AgentAvatar` dataclass (`agent_name: str`, `content_type: str`, `updated_at: int`) and `AgentAvatarStore` with `put(agent_name, data, content_type) -> AgentAvatar`, `get(agent_name) -> tuple[bytes, AgentAvatar] | None`, `list_all() -> list[AgentAvatar]`, `delete(agent_name) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/stores/test_agent_avatar_store.py
"""Round-trip and scoping for the fork-local agent avatar store."""

from __future__ import annotations

import pytest

from omnigent.stores.agent_avatar_store import AgentAvatarStore
from omnigent.stores.artifact_store.local import LocalArtifactStore


@pytest.fixture
def store(tmp_path, db_uri):
    return AgentAvatarStore(db_uri, LocalArtifactStore(str(tmp_path / "artifacts")))


def test_put_then_get_round_trips(store):
    meta = store.put("researcher", b"\x89PNG-bytes", "image/png")
    assert meta.agent_name == "researcher"
    assert meta.content_type == "image/png"
    got = store.get("researcher")
    assert got is not None
    data, meta2 = got
    assert data == b"\x89PNG-bytes"
    assert meta2.updated_at == meta.updated_at


def test_get_unknown_agent_is_none(store):
    assert store.get("nope") is None


def test_put_twice_replaces_and_advances_updated_at(store):
    first = store.put("researcher", b"one", "image/png")
    second = store.put("researcher", b"two", "image/jpeg")
    assert store.get("researcher")[0] == b"two"
    assert second.content_type == "image/jpeg"
    assert second.updated_at >= first.updated_at
    assert len(store.list_all()) == 1


def test_delete_removes_row_and_blob(store):
    store.put("researcher", b"one", "image/png")
    assert store.delete("researcher") is True
    assert store.get("researcher") is None
    assert store.delete("researcher") is False


def test_avatars_are_scoped_to_their_workspace(store):
    """One workspace must not read another's avatar.

    Every query in this store filters on ``current_workspace_id()`` and the
    artifact key embeds the workspace id, so the same agent NAME in two
    workspaces is two different pictures. Without this the drawer would
    leak images across tenants — the agent name is not unique globally.
    """
    from omnigent.db.db_models import workspace_scope

    with workspace_scope(1):
        store.put("researcher", b"workspace-one", "image/png")
    with workspace_scope(2):
        assert store.get("researcher") is None
        assert store.list_all() == []
        store.put("researcher", b"workspace-two", "image/png")
        assert store.get("researcher")[0] == b"workspace-two"
    with workspace_scope(1):
        assert store.get("researcher")[0] == b"workspace-one"
```

Check the real name of the workspace context manager in `omnigent/db/db_models.py` before writing this (`grep -n "workspace_scope\|current_workspace_id" omnigent/db/db_models.py`) — it is referenced from `host_store.py` too. Use whatever that file actually exports; do not introduce a new one.

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/python -m pytest tests/stores/test_agent_avatar_store.py -v`
Expected: FAIL — `ModuleNotFoundError: omnigent.stores.agent_avatar_store`

- [ ] **Step 3: Implement the store**

```python
# omnigent/stores/agent_avatar_store.py
"""Fork-local store mapping an agent name to an avatar image.

airbrx-only. The image BYTES live in the existing artifact store — which
already has local, S3 and Databricks-volumes backends, so avatars work on
every deployment shape without a new storage mechanism. This module owns
only the mapping row and keeps the two in step.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import Engine, select

from omnigent.db.db_models import SqlAgentAvatar, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_named_managed_session_maker
from omnigent.stores.artifact_store import ArtifactStore


@dataclass(frozen=True)
class AgentAvatar:
    """Metadata for one agent's avatar (no bytes)."""

    agent_name: str
    content_type: str
    updated_at: int


def _artifact_key(workspace_id: int, agent_name: str) -> str:
    """Stable artifact key for an agent's avatar."""
    return f"agent-avatars/{workspace_id}/{agent_name}"


class AgentAvatarStore:
    """Avatar metadata in SQL, avatar bytes in the artifact store.

    :param storage_location: SQLAlchemy database URI.
    :param artifact_store: Blob backend for the image bytes.
    """

    def __init__(self, storage_location: str, artifact_store: ArtifactStore) -> None:
        self._engine: Engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.agent_avatar_store",
        )
        self._artifacts = artifact_store

    def put(self, agent_name: str, data: bytes, content_type: str) -> AgentAvatar:
        """Store (or replace) an agent's avatar.

        Bytes are written BEFORE the row is committed: a blob with no row
        is invisible and harmless, whereas a row pointing at a missing
        blob would render as a broken image.
        """
        workspace_id = current_workspace_id()
        key = _artifact_key(workspace_id, agent_name)
        self._artifacts.put(key, data)
        now = int(time.time())
        with self._session("put_agent_avatar") as session:
            row = session.get(SqlAgentAvatar, (workspace_id, agent_name))
            if row is None:
                row = SqlAgentAvatar(
                    workspace_id=workspace_id,
                    agent_name=agent_name,
                    artifact_key=key,
                    content_type=content_type,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.artifact_key = key
                row.content_type = content_type
                row.updated_at = now
        return AgentAvatar(agent_name, content_type, now)

    def get(self, agent_name: str) -> tuple[bytes, AgentAvatar] | None:
        """Return ``(bytes, metadata)``, or ``None`` when unset.

        A row whose blob has gone missing is treated as unset rather than
        raising — the caller renders the initials fallback.
        """
        with self._session("get_agent_avatar") as session:
            row = session.get(SqlAgentAvatar, (current_workspace_id(), agent_name))
            if row is None:
                return None
            meta = AgentAvatar(row.agent_name, row.content_type, row.updated_at)
            key = row.artifact_key
        try:
            return self._artifacts.get(key), meta
        except KeyError:
            return None

    def list_all(self) -> list[AgentAvatar]:
        """Every avatar in this workspace, for the drawer's one-shot fetch."""
        with self._session("list_agent_avatars") as session:
            rows = session.execute(
                select(SqlAgentAvatar)
                .where(SqlAgentAvatar.workspace_id == current_workspace_id())
                .order_by(SqlAgentAvatar.agent_name.asc())
            ).scalars()
            return [AgentAvatar(r.agent_name, r.content_type, r.updated_at) for r in rows]

    def delete(self, agent_name: str) -> bool:
        """Remove an avatar. ``False`` when there was none."""
        workspace_id = current_workspace_id()
        with self._session("delete_agent_avatar") as session:
            row = session.get(SqlAgentAvatar, (workspace_id, agent_name))
            if row is None:
                return False
            key = row.artifact_key
            session.delete(row)
        try:
            self._artifacts.delete(key)
        except KeyError:
            pass
        return True
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/stores/test_agent_avatar_store.py -v`
Expected: PASS (5 tests)

If the `db_uri` fixture is not available in `tests/stores/`, check `tests/conftest.py` for its name and adjust the fixture signature — do not invent a new database fixture.

- [ ] **Step 5: Commit**

```bash
git add omnigent/stores/agent_avatar_store.py tests/stores/test_agent_avatar_store.py
git commit -m "feat(stores): agent avatar store over the artifact store"
```

---

### Task 3: `/v1/agent-avatars` routes

**Files:**
- Create: `omnigent/server/routes/agent_avatars.py`
- Modify: `omnigent/server/app.py` (one `include_router`, next to the harnesses one near line 2670)
- Modify: `openapi.json` (regenerated)
- Test: `tests/server/integration/test_agent_avatars_api.py`

**Interfaces:**
- Consumes: `AgentAvatarStore` (Task 2).
- Produces: `create_agent_avatars_router(avatar_store, *, auth_provider=None) -> APIRouter`. Endpoints: `GET /v1/agent-avatars` → `{"data": [{"agent_name", "url", "updated_at"}]}`; `GET /v1/agent-avatars/{agent_name}` → image bytes; `PUT /v1/agent-avatars/{agent_name}` (multipart `file`) → metadata; `DELETE /v1/agent-avatars/{agent_name}` → 204.

- [ ] **Step 1: Write the failing test**

```python
# tests/server/integration/test_agent_avatars_api.py
"""Upload, serve, list and delete agent avatars."""

from __future__ import annotations

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


async def test_put_then_get_serves_the_image(avatar_client):
    r = await avatar_client.put(
        "/v1/agent-avatars/researcher",
        files={"file": ("a.png", PNG, "image/png")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["agent_name"] == "researcher"

    img = await avatar_client.get("/v1/agent-avatars/researcher")
    assert img.status_code == 200
    assert img.content == PNG
    assert img.headers["content-type"] == "image/png"


async def test_get_unknown_agent_is_404(avatar_client):
    r = await avatar_client.get("/v1/agent-avatars/nobody")
    assert r.status_code == 404


async def test_rejects_non_image_content_type(avatar_client):
    r = await avatar_client.put(
        "/v1/agent-avatars/researcher",
        files={"file": ("x.svg", b"<svg/>", "image/svg+xml")},
    )
    assert r.status_code == 400, r.text
    assert "content type" in r.text.lower()


async def test_rejects_oversized_image(avatar_client):
    r = await avatar_client.put(
        "/v1/agent-avatars/researcher",
        files={"file": ("big.png", b"0" * (2 * 1024 * 1024 + 1), "image/png")},
    )
    assert r.status_code == 400, r.text
    assert "too large" in r.text.lower()


async def test_list_and_delete(avatar_client):
    await avatar_client.put(
        "/v1/agent-avatars/researcher",
        files={"file": ("a.png", PNG, "image/png")},
    )
    listed = await avatar_client.get("/v1/agent-avatars")
    assert [a["agent_name"] for a in listed.json()["data"]] == ["researcher"]
    assert listed.json()["data"][0]["url"] == "/v1/agent-avatars/researcher"

    assert (await avatar_client.delete("/v1/agent-avatars/researcher")).status_code == 204
    assert (await avatar_client.get("/v1/agent-avatars")).json()["data"] == []
    assert (await avatar_client.delete("/v1/agent-avatars/researcher")).status_code == 404
```

Add an `avatar_client` fixture in the same file, modelled on the app fixtures already in `tests/server/integration/test_hosts_api.py` — build a `FastAPI()`, `include_router(create_agent_avatars_router(store), prefix="/v1")`, and wrap it in `AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`. Read that file first and copy its fixture shape rather than inventing one.

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/python -m pytest tests/server/integration/test_agent_avatars_api.py -v`
Expected: FAIL — `ModuleNotFoundError: omnigent.server.routes.agent_avatars`

- [ ] **Step 3: Implement the router**

```python
# omnigent/server/routes/agent_avatars.py
"""Fork-local routes serving agent avatars for the agent drawer.

airbrx-only, and deliberately its own module: a file upstream does not
have can never conflict on a sync. The only upstream-owned line this
feature adds is the ``include_router`` call in ``app.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.stores.agent_avatar_store import AgentAvatarStore

#: Raster formats only. SVG is excluded on purpose — it is an active
#: document (script, external refs) served from our own origin, so a
#: hostile upload would be stored XSS against every drawer viewer.
ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})

#: 2 MiB. An avatar renders at ~40px; anything larger is a mistake, and
#: the cap keeps a bad upload from filling the artifact store.
MAX_AVATAR_BYTES = 2 * 1024 * 1024


def create_agent_avatars_router(
    avatar_store: AgentAvatarStore,
    *,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for ``/v1/agent-avatars``."""
    router = APIRouter()

    @router.get("/agent-avatars")
    async def list_agent_avatars(request: Request) -> dict[str, Any]:
        require_user(request, auth_provider)
        return {
            "data": [
                {
                    "agent_name": a.agent_name,
                    "url": f"/v1/agent-avatars/{a.agent_name}",
                    "updated_at": a.updated_at,
                }
                for a in avatar_store.list_all()
            ]
        }

    @router.get("/agent-avatars/{agent_name}")
    async def get_agent_avatar(request: Request, agent_name: str) -> Response:
        require_user(request, auth_provider)
        found = avatar_store.get(agent_name)
        if found is None:
            return JSONResponse(
                {"error": {"code": "not_found", "message": "no avatar for that agent"}},
                status_code=404,
            )
        data, meta = found
        # Keyed on updated_at so a re-upload busts the cache immediately
        # while an unchanged avatar is never refetched.
        return Response(
            content=data,
            media_type=meta.content_type,
            headers={
                "Cache-Control": "private, max-age=300",
                "ETag": f'"{meta.updated_at}"',
            },
        )

    @router.put("/agent-avatars/{agent_name}")
    async def put_agent_avatar(
        request: Request, agent_name: str, file: UploadFile
    ) -> Response:
        require_user(request, auth_provider)
        if file.content_type not in ALLOWED_CONTENT_TYPES:
            return JSONResponse(
                {
                    "error": {
                        "code": "invalid_input",
                        "message": (
                            f"unsupported content type {file.content_type!r}; "
                            f"allowed: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
                        ),
                    }
                },
                status_code=400,
            )
        data = await file.read()
        if len(data) > MAX_AVATAR_BYTES:
            return JSONResponse(
                {
                    "error": {
                        "code": "invalid_input",
                        "message": f"avatar too large ({len(data)} bytes, max {MAX_AVATAR_BYTES})",
                    }
                },
                status_code=400,
            )
        meta = avatar_store.put(agent_name, data, file.content_type)
        return JSONResponse(
            {
                "agent_name": meta.agent_name,
                "url": f"/v1/agent-avatars/{meta.agent_name}",
                "updated_at": meta.updated_at,
            }
        )

    @router.delete("/agent-avatars/{agent_name}")
    async def delete_agent_avatar(request: Request, agent_name: str) -> Response:
        require_user(request, auth_provider)
        if not avatar_store.delete(agent_name):
            return JSONResponse(
                {"error": {"code": "not_found", "message": "no avatar for that agent"}},
                status_code=404,
            )
        return Response(status_code=204)

    return router
```

- [ ] **Step 4: Wire it through `create_app` and `cli.py`**

`create_app` has **no `db_uri` parameter** — every store is constructed in `cli.py` and passed in. Follow that pattern exactly; do not construct the store inside `app.py`.

In `omnigent/server/app.py`, add a parameter to `create_app` beside the other optional stores (near `host_store: HostStore | None = None`, ~line 1078):

```python
    agent_avatar_store: AgentAvatarStore | None = None,
```

Document it in the function's docstring in the same style as the neighbouring `:param host_store:` entry. Then register the router immediately after the harnesses `include_router` (~line 2670), guarded like the other optional stores:

```python
    if agent_avatar_store is not None:
        app.include_router(
            create_agent_avatars_router(
                agent_avatar_store,
                auth_provider=auth_provider,
            ),
            prefix="/v1",
            tags=["agent-avatars"],
        )
```

In `omnigent/cli.py`, construct it where the other stores are built for the server command — find the block that constructs `host_store` and add alongside it, reusing the `db_uri` and artifact-store variables already in scope there:

```python
    agent_avatar_store = AgentAvatarStore(db_uri, artifact_store)
```

then pass `agent_avatar_store=agent_avatar_store` in the `create_app(...)` call. Read the surrounding lines and match the real variable names — they may differ from these.

- [ ] **Step 5: Run the tests and regenerate the spec**

```bash
.venv/bin/python -m pytest tests/server/integration/test_agent_avatars_api.py -v
.venv/bin/python scripts/dump_openapi.py
.venv/bin/python -m ruff check omnigent tests && .venv/bin/python -m ruff format omnigent tests
```
Expected: 5 tests PASS; `openapi.json` gains the four paths.

- [ ] **Step 6: Commit**

```bash
git add omnigent/server/routes/agent_avatars.py omnigent/server/app.py openapi.json tests/server/integration/test_agent_avatars_api.py
git commit -m "feat(api): /v1/agent-avatars upload, serve, list and delete"
```

---

### Task 4: Initials fallback + avatar hook

**Files:**
- Create: `web/src/lib/agentInitials.ts`
- Create: `web/src/hooks/useAgentAvatars.ts`
- Test: `web/src/lib/agentInitials.test.ts`

**Interfaces:**
- Consumes: `GET /v1/agent-avatars` (Task 3).
- Produces: `agentInitials(name: string): string`, `agentAvatarColor(name: string): string`, and `useAgentAvatars(): { data?: Record<string, string> }` mapping agent name → image URL.

- [ ] **Step 1: Write the failing test**

```ts
// web/src/lib/agentInitials.test.ts
import { describe, expect, it } from "vitest";

import { agentAvatarColor, agentInitials } from "./agentInitials";

describe("agentInitials", () => {
  it("takes the first letter of the first two words", () => {
    expect(agentInitials("cache cow")).toBe("CC");
  });

  it("takes the first two letters of a single word", () => {
    expect(agentInitials("researcher")).toBe("RE");
  });

  it("handles separators and extra whitespace", () => {
    expect(agentInitials("  cache-hound  ")).toBe("CH");
    expect(agentInitials("cache_register")).toBe("CR");
  });

  it("falls back to ? on an empty name", () => {
    expect(agentInitials("")).toBe("?");
    expect(agentInitials("   ")).toBe("?");
  });
});

describe("agentAvatarColor", () => {
  it("is deterministic for the same name", () => {
    expect(agentAvatarColor("researcher")).toBe(agentAvatarColor("researcher"));
  });

  it("distinguishes different names", () => {
    expect(agentAvatarColor("alpha")).not.toBe(agentAvatarColor("zulu"));
  });
});
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd web && npx pnpm@11.15.1 exec vitest run src/lib/agentInitials.test.ts`
Expected: FAIL — cannot resolve `./agentInitials`

- [ ] **Step 3: Implement**

```ts
// web/src/lib/agentInitials.ts
// Deterministic initials + colour for an agent with no uploaded avatar, so
// the drawer reads as a roster of distinct teammates on day one rather than
// a column of identical grey circles.

const PALETTE = [
  "bg-sky-600",
  "bg-emerald-600",
  "bg-violet-600",
  "bg-amber-600",
  "bg-rose-600",
  "bg-teal-600",
  "bg-indigo-600",
  "bg-orange-600",
] as const;

/** Up to two uppercase letters for an agent name; "?" when there are none. */
export function agentInitials(name: string): string {
  const words = name.split(/[\s\-_]+/u).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

/** Stable Tailwind background class for a name. Same name, same colour. */
export function agentAvatarColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0;
  }
  return PALETTE[Math.abs(hash) % PALETTE.length];
}
```

```ts
// web/src/hooks/useAgentAvatars.ts
import { useQuery } from "@tanstack/react-query";

import { authenticatedFetch } from "@/lib/identity";

interface AgentAvatarRow {
  agent_name: string;
  url: string;
  updated_at: number;
}

/**
 * Agent name -> avatar URL for every agent that has one.
 *
 * One request for the whole roster rather than one per row: the drawer
 * renders the full list at once, and N image-metadata requests would be
 * N round trips before the first pixel.
 */
export function useAgentAvatars() {
  return useQuery({
    queryKey: ["agent-avatars"],
    queryFn: async (): Promise<Record<string, string>> => {
      const res = await authenticatedFetch("/v1/agent-avatars");
      if (!res.ok) return {};
      const body = (await res.json()) as { data: AgentAvatarRow[] };
      return Object.fromEntries(
        body.data.map((a) => [a.agent_name, `${a.url}?v=${a.updated_at}`]),
      );
    },
    staleTime: 60_000,
  });
}
```

`authenticatedFetch` is the helper every sibling hook uses (`web/src/hooks/useHosts.ts:2`). Confirm its exact signature there before writing — if it takes options or returns a parsed body rather than a `Response`, match that.

An avatar-less roster is the normal state, not an error: a non-OK response returns `{}` so the drawer falls back to initials rather than showing an error state.

- [ ] **Step 4: Run the tests**

Run: `cd web && npx pnpm@11.15.1 exec vitest run src/lib/agentInitials.test.ts`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/agentInitials.ts web/src/lib/agentInitials.test.ts web/src/hooks/useAgentAvatars.ts
git commit -m "feat(web): deterministic agent initials fallback and avatar hook"
```

---

### Task 5: `AgentDrawer` component

**Files:**
- Create: `web/src/shell/AgentDrawer.tsx`
- Test: `web/src/shell/AgentDrawer.test.tsx`

**Interfaces:**
- Consumes: `useAgentAvatars` (Task 4), `agentInitials`, `agentAvatarColor`, and the existing `useAvailableAgents` hook.
- Produces: `<AgentDrawer open onClose onSelectAgent />` where `onSelectAgent: (agentName: string) => void`.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/shell/AgentDrawer.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentDrawer } from "./AgentDrawer";

vi.mock("@/hooks/useAvailableAgents", () => ({ useAvailableAgents: vi.fn() }));
vi.mock("@/hooks/useAgentAvatars", () => ({ useAgentAvatars: vi.fn() }));

import { useAgentAvatars } from "@/hooks/useAgentAvatars";
import { useAvailableAgents } from "@/hooks/useAvailableAgents";

function renderDrawer(onSelectAgent = vi.fn(), open = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AgentDrawer open={open} onClose={vi.fn()} onSelectAgent={onSelectAgent} />
    </QueryClientProvider>,
  );
  return onSelectAgent;
}

beforeEach(() => {
  vi.mocked(useAvailableAgents).mockReturnValue({
    data: [
      { id: "a1", name: "researcher", display_name: "Researcher", description: "Reads things" },
      { id: "a2", name: "cache cow", display_name: "", description: "Harvests leads" },
    ],
  } as never);
  vi.mocked(useAgentAvatars).mockReturnValue({ data: {} } as never);
});

describe("AgentDrawer", () => {
  it("lists every agent with its description", () => {
    renderDrawer();
    expect(screen.getByText("Researcher")).toBeInTheDocument();
    expect(screen.getByText("Reads things")).toBeInTheDocument();
    expect(screen.getByText("Harvests leads")).toBeInTheDocument();
  });

  it("falls back to name when display_name is empty", () => {
    renderDrawer();
    expect(screen.getByText("cache cow")).toBeInTheDocument();
  });

  it("renders initials when an agent has no avatar", () => {
    renderDrawer();
    expect(screen.getByText("RE")).toBeInTheDocument();
    expect(screen.getByText("CC")).toBeInTheDocument();
  });

  it("renders the image when an agent has an avatar", () => {
    vi.mocked(useAgentAvatars).mockReturnValue({
      data: { researcher: "/v1/agent-avatars/researcher?v=1" },
    } as never);
    renderDrawer();
    const img = screen.getByAltText("researcher");
    expect(img).toHaveAttribute("src", "/v1/agent-avatars/researcher?v=1");
    expect(screen.queryByText("RE")).not.toBeInTheDocument();
  });

  it("calls onSelectAgent with the agent name when a row is clicked", () => {
    const onSelect = renderDrawer();
    fireEvent.click(screen.getByTestId("agent-drawer-row-researcher"));
    expect(onSelect).toHaveBeenCalledWith("researcher");
  });

  it("renders nothing when closed", () => {
    renderDrawer(vi.fn(), false);
    expect(screen.queryByTestId("agent-drawer")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd web && npx pnpm@11.15.1 exec vitest run src/shell/AgentDrawer.test.tsx`
Expected: FAIL — cannot resolve `./AgentDrawer`

- [ ] **Step 3: Implement**

```tsx
// web/src/shell/AgentDrawer.tsx
// Slide-out roster of every registered agent, opened from the sidebar.
//
// Deliberately a sibling of the new-chat picker rather than a replacement
// for it: NewChatDialog.tsx conflicted in BOTH stages of the v0.11 -> v0.13
// upstream sync, so this feature keeps its footprint in upstream-owned
// files to a trigger button and one mount point.

import { useAgentAvatars } from "@/hooks/useAgentAvatars";
import { useAvailableAgents } from "@/hooks/useAvailableAgents";
import { agentAvatarColor, agentInitials } from "@/lib/agentInitials";
import { cn } from "@/lib/utils";

interface AgentDrawerProps {
  open: boolean;
  onClose: () => void;
  /** Called with the agent's NAME — the key avatars are stored under. */
  onSelectAgent: (agentName: string) => void;
}

export function AgentDrawer({ open, onClose, onSelectAgent }: AgentDrawerProps) {
  const { data: agents } = useAvailableAgents();
  const { data: avatars } = useAgentAvatars();

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/40"
        onClick={onClose}
        data-testid="agent-drawer-scrim"
      />
      <aside
        data-testid="agent-drawer"
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-80 max-w-[85vw] flex-col",
          "border-r bg-background shadow-xl transition-transform",
        )}
      >
        <header className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="text-sm font-semibold">Agents</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            aria-label="Close agents"
          >
            ✕
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-2">
          {(agents ?? []).map((agent) => {
            const url = avatars?.[agent.name];
            return (
              <button
                key={agent.id}
                type="button"
                data-testid={`agent-drawer-row-${agent.name}`}
                onClick={() => onSelectAgent(agent.name)}
                className="flex w-full items-center gap-3 rounded-md p-2 text-left hover:bg-accent"
              >
                {url ? (
                  <img
                    src={url}
                    alt={agent.name}
                    className="size-10 shrink-0 rounded-full object-cover"
                  />
                ) : (
                  <span
                    aria-hidden="true"
                    className={cn(
                      "flex size-10 shrink-0 items-center justify-center rounded-full",
                      "text-xs font-semibold text-white",
                      agentAvatarColor(agent.name),
                    )}
                  >
                    {agentInitials(agent.name)}
                  </span>
                )}
                <span className="min-w-0">
                  {/* Label from display_name so the drawer reads the same as
                      the picker; the avatar and onSelectAgent both key on
                      `name`, which is what the server stores. */}
                  <span className="block truncate text-sm font-medium">
                    {agent.display_name || agent.name}
                  </span>
                  {agent.description ? (
                    <span className="block truncate text-xs text-muted-foreground">
                      {agent.description}
                    </span>
                  ) : null}
                </span>
              </button>
            );
          })}
        </div>
      </aside>
    </>
  );
}
```

- [ ] **Step 4: Run the tests and lint**

```bash
cd web && npx pnpm@11.15.1 exec vitest run src/shell/AgentDrawer.test.tsx && npx pnpm@11.15.1 run lint
```
Expected: 6 tests PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/shell/AgentDrawer.tsx web/src/shell/AgentDrawer.test.tsx
git commit -m "feat(web): AgentDrawer roster with avatars and initials fallback"
```

---

### Task 6: Trigger and mount

**Files:**
- Modify: `web/src/shell/Sidebar.tsx` (button beside `data-testid="new-chat-button"`, ~line 1091)
- Modify: `web/src/shell/AppShell.tsx` (state + mount, alongside the `FilesPanelDrawer` mount ~line 2165)
- Test: `web/src/shell/AgentDrawer.integration.test.tsx`

**Interfaces:**
- Consumes: `<AgentDrawer>` (Task 5).
- Produces: nothing further.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/shell/AgentDrawer.integration.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { AgentDrawer } from "./AgentDrawer";

vi.mock("@/hooks/useAvailableAgents", () => ({
  useAvailableAgents: () => ({ data: [{ id: "a1", name: "researcher", display_name: "Researcher", description: "" }] }),
}));
vi.mock("@/hooks/useAgentAvatars", () => ({ useAgentAvatars: () => ({ data: {} }) }));

// Mirrors the AppShell wiring: a trigger toggles `open`, selecting a row
// closes the drawer and hands the agent name to the new-chat flow.
function Harness({ onSelect }: { onSelect: (n: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" data-testid="browse-agents-button" onClick={() => setOpen(true)}>
        Agents
      </button>
      <AgentDrawer
        open={open}
        onClose={() => setOpen(false)}
        onSelectAgent={(n) => {
          setOpen(false);
          onSelect(n);
        }}
      />
    </>
  );
}

describe("agent drawer wiring", () => {
  it("opens from the trigger and closes on select, reporting the agent", () => {
    const onSelect = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <Harness onSelect={onSelect} />
      </QueryClientProvider>,
    );

    expect(screen.queryByTestId("agent-drawer")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("browse-agents-button"));
    expect(screen.getByTestId("agent-drawer")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("agent-drawer-row-researcher"));
    expect(onSelect).toHaveBeenCalledWith("researcher");
    expect(screen.queryByTestId("agent-drawer")).not.toBeInTheDocument();
  });

  it("closes when the scrim is clicked", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <Harness onSelect={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByTestId("browse-agents-button"));
    fireEvent.click(screen.getByTestId("agent-drawer-scrim"));
    expect(screen.queryByTestId("agent-drawer")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd web && npx pnpm@11.15.1 exec vitest run src/shell/AgentDrawer.integration.test.tsx`
Expected: FAIL until Task 5's component exists; if Task 5 is done this passes immediately — that is fine, it is the contract the wiring must honour.

- [ ] **Step 3: Add the sidebar trigger**

In `web/src/shell/Sidebar.tsx`, beside the element carrying `data-testid="new-chat-button"` (~line 1091), add a sibling button. Match the surrounding button's class names exactly — read them first:

```tsx
<button
  type="button"
  data-testid="browse-agents-button"
  aria-label="Browse agents"
  onClick={onBrowseAgents}
>
  <UsersIcon className="size-3.5 shrink-0 text-muted-foreground" />
</button>
```

Add `onBrowseAgents: () => void` to the Sidebar props interface and import `UsersIcon` from the same icon package as the neighbouring `PlusIcon` (line 48).

- [ ] **Step 4: Mount the drawer in AppShell**

In `web/src/shell/AppShell.tsx`: import `AgentDrawer`, add `const [agentDrawerOpen, setAgentDrawerOpen] = useState(false);` beside the other drawer state (~line 360), pass `onBrowseAgents={() => setAgentDrawerOpen(true)}` to `<Sidebar>`, and mount beside the `FilesPanelDrawer` (~line 2165):

```tsx
<AgentDrawer
  open={agentDrawerOpen}
  onClose={() => setAgentDrawerOpen(false)}
  onSelectAgent={(agentName) => {
    setAgentDrawerOpen(false);
    startNewChatWithAgent(agentName);
  }}
/>
```

`startNewChatWithAgent` must route through the SAME create path the landing picker already uses, so the two cannot drift. Find that call site in `NewChatDialog.tsx` and reuse it — do not write a second create path. If no reusable function exists, extract the existing one rather than duplicating it.

- [ ] **Step 5: Run the full web suite and lint**

```bash
cd web && npx pnpm@11.15.1 run lint && npx pnpm@11.15.1 run test
```
Expected: lint clean; all tests pass. Any existing suite that renders `Sidebar` and now fails on the new required prop must have it added to its props — do not make the prop optional to dodge that.

- [ ] **Step 6: Commit**

```bash
git add web/src/shell/Sidebar.tsx web/src/shell/AppShell.tsx web/src/shell/AgentDrawer.integration.test.tsx
git commit -m "feat(web): open the agent drawer from the sidebar"
```

---

## Out of scope

Slice 3 — the cost/usage surface (agent token spend, subscription cost avoided, warehouse cost via Airbrx vs direct). See the spec's closing section, including the note that `airbrx-cost-analysis` already does per-tenant attribution and the counterfactual needs a defensible methodology before any number reaches a screen.
