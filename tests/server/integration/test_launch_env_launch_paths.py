"""Every runner launch path carries the agent's launch environment.

One test per path, named for the path, because the first version of this
mechanism (#78) was wired into one launch site of three and its one test
covered that site. The other two sent a frame with no ``agent_env`` and no
``agent_secret_env``, and an agent whose credential is a host secret
reference (Eva's outreach bearer token) failed turn setup on every session
launched through them. Found in production on 2026-09-24 by removing the
host-wide passthrough that had been hiding it.

The paths:

* **session create**: ``POST /v1/sessions`` with ``host_id``, the inline launch.
* **host runners**: ``POST /v1/hosts/{id}/runners``, which resume, switch host
  and fork use.
* **relaunch**: a message for a session whose runner is gone (the Mac slept,
  the host restarted, the runner was reaped).

Each registers a launch-environment provider for a test agent and asserts on
the ``host.launch_runner`` frame the fake host actually received.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI

from omnigent.runtime import launch_env
from omnigent.runtime.launch_env import LaunchEnv
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from tests.server.helpers import create_test_agent
from tests.server.integration.test_session_host_launch import (  # noqa: F401
    _HOST_ID,
    _WORKSPACE,
    _connect_host,
    _serve_one_launch,
    app,
)

pytestmark = pytest.mark.asyncio

AGENT = "launch-env-agent"
PLAIN = {"OUTREACH_MCP_URL": "http://127.0.0.1:8000/mcp/"}
SECRET = {"OUTREACH_MCP_TOKEN": "keychain:eva-outreach-token"}


@pytest.fixture()
def provider() -> Iterator[list[tuple[str, str | None]]]:
    """A provider for AGENT only, recording who each launch was collected for."""
    seen: list[tuple[str, str | None]] = []

    def answer(name: str, user: str | None) -> LaunchEnv | None:
        if name != AGENT:
            return None
        seen.append((name, user))
        return LaunchEnv(env=dict(PLAIN), secret_refs=dict(SECRET))

    launch_env.reset()
    launch_env.register(answer)
    try:
        yield seen
    finally:
        launch_env.reset()


def _assert_carries_launch_env(frame, seen: list[tuple[str, str | None]]) -> None:
    assert frame.agent_env == PLAIN, "the plain value (the MCP URL) was not sent"
    assert frame.agent_secret_env == SECRET, "the secret reference was not sent"
    assert seen, "the provider was never asked"


async def _create_session(client: httpx.AsyncClient, comm, agent_id: str) -> dict:
    responder = asyncio.create_task(_serve_one_launch(comm, launch_status="launched"))
    resp = await client.post(
        "/v1/sessions",
        json={"agent_id": agent_id, "host_id": _HOST_ID, "workspace": _WORKSPACE},
    )
    frame = await responder
    assert resp.status_code == 201, resp.text
    return {"session": resp.json(), "frame": frame}


async def test_session_create_launch_carries_the_launch_env(
    client: httpx.AsyncClient,
    app: FastAPI,  # noqa: F811
    provider: list[tuple[str, str | None]],
) -> None:
    comm = await _connect_host(app)
    agent = await create_test_agent(client, name=AGENT)
    created = await _create_session(client, comm, agent["id"])
    _assert_carries_launch_env(created["frame"], provider)


async def test_host_runners_launch_carries_the_launch_env(
    client: httpx.AsyncClient,
    app: FastAPI,  # noqa: F811
    db_uri: str,
    provider: list[tuple[str, str | None]],
) -> None:
    """``POST /v1/hosts/{id}/runners``: resume, switch host and fork."""
    comm = await _connect_host(app)
    agent = await create_test_agent(client, name=AGENT)
    # An unbound session for this agent, the state resume-with-directory binds.
    conv = SqlAlchemyConversationStore(db_uri).create_conversation(agent_id=agent["id"])
    responder = asyncio.create_task(_serve_one_launch(comm, launch_status="launched"))
    resp = await client.post(
        f"/v1/hosts/{_HOST_ID}/runners",
        json={"session_id": conv.id, "workspace": _WORKSPACE},
    )
    frame = await responder
    assert resp.status_code == 200, resp.text
    _assert_carries_launch_env(frame, provider)


async def test_relaunch_after_the_runner_is_gone_carries_the_launch_env(
    client: httpx.AsyncClient,
    app: FastAPI,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    provider: list[tuple[str, str | None]],
) -> None:
    """The path that runs every time a Mac wakes: a message relaunches the runner."""
    from omnigent.runtime import set_runner_client
    from omnigent.server.routes import sessions as sessions_module
    from omnigent.server.routes.sessions import routes_events as routes_events_module

    monkeypatch.setattr(sessions_module, "_HOST_BOUND_RUNNER_CONNECT_GRACE_S", 0.0)
    monkeypatch.setattr(routes_events_module, "_HOST_RELAUNCH_RUNNER_CONNECT_TIMEOUT_S", 0.0)
    comm = await _connect_host(app)
    agent = await create_test_agent(client, name=AGENT)
    created = await _create_session(client, comm, agent["id"])
    session_id = created["session"]["id"]
    provider.clear()

    # No runner ever connected, so the next message has to relaunch one.
    set_runner_client(None)
    relaunch = asyncio.create_task(_serve_one_launch(comm, launch_status="launched"))
    try:
        await client.post(
            f"/v1/sessions/{session_id}/events",
            json={
                "type": "message",
                "data": {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            },
        )
    finally:
        frame = await relaunch
        set_runner_client(None)
    assert frame.session_id == session_id
    _assert_carries_launch_env(frame, provider)
