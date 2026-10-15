"""Upload, serve, list and delete agent avatars."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from omnigent.errors import OmnigentError
from omnigent.server.auth import UnifiedAuthProvider
from omnigent.server.routes.agent_avatars import create_agent_avatars_router
from omnigent.stores.agent_avatar_store import AgentAvatarStore
from omnigent.stores.artifact_store.local import LocalArtifactStore

pytestmark = pytest.mark.asyncio

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture()
def avatar_store(db_uri: str, tmp_path: Path) -> AgentAvatarStore:
    """Avatar store over the shared test SQLite db and a local artifact dir."""
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return AgentAvatarStore(db_uri, artifact_store)


@pytest.fixture()
def avatar_app(avatar_store: AgentAvatarStore) -> FastAPI:
    """Minimal app mounting only the agent-avatars router at ``/v1``."""
    app = FastAPI()
    app.include_router(
        create_agent_avatars_router(avatar_store),
        prefix="/v1",
    )
    return app


@pytest_asyncio.fixture()
async def avatar_client(avatar_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client wired to the agent-avatars app."""
    transport = httpx.ASGITransport(app=avatar_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
def unauth_avatar_app(avatar_store: AgentAvatarStore) -> FastAPI:
    """Same router, but wired to a real, active auth provider.

    ``UnifiedAuthProvider(source="header", local_single_user=False)`` reads
    the trusted ``X-Forwarded-Email`` header and, with single-user fallback
    disabled, returns ``None`` for a request that doesn't carry it — so
    ``require_user`` raises and every route below must reject the call
    before it ever reaches the store. Registers the same ``OmnigentError``
    -> JSON conversion ``create_app`` installs in production (mirrors
    ``multi_user_app`` in ``tests/server/integration/test_hosts_api.py``),
    since a bare ``FastAPI()`` app has no handler for it otherwise.
    """
    app = FastAPI()
    app.include_router(
        create_agent_avatars_router(
            avatar_store,
            auth_provider=UnifiedAuthProvider(source="header", local_single_user=False),
        ),
        prefix="/v1",
    )

    @app.exception_handler(OmnigentError)
    async def _handle_omnigent_error(request: Request, exc: OmnigentError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    return app


@pytest_asyncio.fixture()
async def unauth_avatar_client(unauth_avatar_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client wired to the auth-gated agent-avatars app, no credentials sent."""
    transport = httpx.ASGITransport(app=unauth_avatar_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


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
    # Finding 2: served image responses must carry nosniff — the stored
    # type is derived from magic bytes, but the browser must still be
    # told not to second-guess it.
    assert img.headers["x-content-type-options"] == "nosniff"


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
    # Finding 1: the upload is read in capped chunks via
    # _read_upload_capped, which raises HTTP 413 as soon as the cap is
    # crossed — superseding the brief's original 400 for this case, since
    # 413 (Payload Too Large) is what the shared read helper (and the
    # rest of the codebase's upload paths) already use for this condition.
    r = await avatar_client.put(
        "/v1/agent-avatars/researcher",
        files={"file": ("big.png", b"0" * (2 * 1024 * 1024 + 1), "image/png")},
    )
    assert r.status_code == 413, r.text


async def test_rejects_bytes_that_do_not_match_any_image_signature(avatar_client):
    # A client-asserted image/png with no PNG magic bytes must not be
    # trusted and stored as-is (Finding 2).
    r = await avatar_client.put(
        "/v1/agent-avatars/researcher",
        files={"file": ("fake.png", b"not actually a png", "image/png")},
    )
    assert r.status_code == 400, r.text
    assert "image format" in r.text.lower()


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


# ── Finding 3: agent_name validation ────────────────────────────────────────


async def test_put_rejects_dot_agent_name(avatar_client):
    # agent-avatars/<ws>/. would normalize to the workspace's own
    # directory, writing a FILE where every other avatar needs a
    # DIRECTORY and bricking every later PUT in that workspace.
    #
    # A literal "." here would never exercise that: httpx normalizes
    # dot-segments client-side before the request leaves the process, so
    # PUT /v1/agent-avatars/. is actually sent as PUT /v1/agent-avatars
    # (the collection route, which has no PUT) -> 405, never reaching
    # _validate_agent_name at all. The percent-encoded form (%2E) is not
    # a dot-segment as far as URL normalization is concerned, so it
    # survives client-side and arrives at the handler as a literal ".",
    # which is exactly what a non-normalizing client (e.g. `curl
    # --path-as-is`) can send. Do not "simplify" this back to a literal
    # "." — that would silently stop testing the guard.
    r = await avatar_client.put(
        "/v1/agent-avatars/%2E",
        files={"file": ("a.png", PNG, "image/png")},
    )
    assert r.status_code == 400, r.text


async def test_put_rejects_dotdot_agent_name(avatar_client):
    # Same reasoning as test_put_rejects_dot_agent_name: a literal ".."
    # is collapsed by httpx before the request is sent (PUT /v1 in this
    # case), so the encoded form (%2E%2E) is required to actually reach
    # the handler.
    r = await avatar_client.put(
        "/v1/agent-avatars/%2E%2E",
        files={"file": ("a.png", PNG, "image/png")},
    )
    assert r.status_code == 400, r.text


async def test_put_rejects_overlength_agent_name(avatar_client):
    r = await avatar_client.put(
        f"/v1/agent-avatars/{'a' * 256}",
        files={"file": ("a.png", PNG, "image/png")},
    )
    assert r.status_code == 400, r.text


async def test_put_accepts_agent_name_with_a_space(avatar_client):
    # Real fixture data includes "cache cow" — spaces must keep working.
    r = await avatar_client.put(
        "/v1/agent-avatars/cache cow",
        files={"file": ("a.png", PNG, "image/png")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["agent_name"] == "cache cow"
    # Finding 5: the URL is percent-encoded, so the space survives intact.
    assert r.json()["url"] == "/v1/agent-avatars/cache%20cow"

    img = await avatar_client.get("/v1/agent-avatars/cache cow")
    assert img.status_code == 200
    assert img.content == PNG


async def test_get_and_delete_also_reject_invalid_agent_name(avatar_client):
    # %2E, not a literal ".": see test_put_rejects_dot_agent_name for why
    # a literal dot never reaches the handler through httpx.
    assert (await avatar_client.get("/v1/agent-avatars/%2E")).status_code == 400
    assert (await avatar_client.delete("/v1/agent-avatars/%2E")).status_code == 400


# ── Finding 4: auth gating ───────────────────────────────────────────────────


async def test_list_requires_auth(unauth_avatar_client):
    r = await unauth_avatar_client.get("/v1/agent-avatars")
    assert r.status_code == 401, r.text


async def test_get_requires_auth(unauth_avatar_client):
    r = await unauth_avatar_client.get("/v1/agent-avatars/researcher")
    assert r.status_code == 401, r.text


async def test_put_requires_auth(unauth_avatar_client):
    r = await unauth_avatar_client.put(
        "/v1/agent-avatars/researcher",
        files={"file": ("a.png", PNG, "image/png")},
    )
    assert r.status_code == 401, r.text


async def test_delete_requires_auth(unauth_avatar_client):
    r = await unauth_avatar_client.delete("/v1/agent-avatars/researcher")
    assert r.status_code == 401, r.text


async def test_listing_hides_an_avatar_whose_agent_is_gone(avatar_store, db_uri, tmp_path):
    """A reused agent name must not inherit the previous agent's face.

    Avatar rows are keyed by ``agent_name`` and nothing deletes one when the
    agent goes away. The drawer reads only this listing, so filtering it is
    what fixes the behaviour a person can actually see.
    """
    from types import SimpleNamespace

    avatar_store.put("still-here", PNG, "image/png")
    avatar_store.put("long-gone", PNG, "image/png")

    live = {"still-here"}
    agent_store = SimpleNamespace(
        get_by_name=lambda name: SimpleNamespace(id=name) if name in live else None
    )
    app = FastAPI()
    app.include_router(
        create_agent_avatars_router(avatar_store, agent_store=agent_store), prefix="/v1"
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        names = [
            row["agent_name"] for row in (await client.get("/v1/agent-avatars")).json()["data"]
        ]
        assert names == ["still-here"]

        # The bytes are NOT destroyed by a read. A listing that deleted would
        # eventually meet a transiently empty or differently scoped agent
        # store and take pictures that should have lived.
        assert avatar_store.get("long-gone") is not None

        # And the orphan comes back the moment its name is registered again,
        # which is the proof that nothing was silently thrown away.
        live.add("long-gone")
        names = [
            row["agent_name"] for row in (await client.get("/v1/agent-avatars")).json()["data"]
        ]
        assert names == ["long-gone", "still-here"]


async def test_listing_without_an_agent_store_is_unchanged(avatar_client, avatar_store):
    """Every existing caller keeps working: no agent_store, no filtering."""
    avatar_store.put("orphan", PNG, "image/png")
    names = [
        row["agent_name"] for row in (await avatar_client.get("/v1/agent-avatars")).json()["data"]
    ]
    assert "orphan" in names
