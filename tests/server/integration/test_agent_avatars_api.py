"""Upload, serve, list and delete agent avatars."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

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
