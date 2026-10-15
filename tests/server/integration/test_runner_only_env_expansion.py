"""Session create and agent listing for an operator agent that opts out of server expansion.

Reproduces the production failure of 2026-09-24: an operator agent registered
with ``--agent`` whose MCP header is ``Bearer ${TOKEN}``, with ``TOKEN`` unset on
the server (correctly: it is a per-host secret the runner resolves). Registration
passed, and every ``POST /v1/sessions`` answered 400 "Unresolved environment
variable", because each server-side load expanded operator bundles against the
server's own environment. ``GET /v1/agents`` did the same.

Registration goes through the real ``omnigent.cli._preregister_agent`` so the
agent is exactly what an operator's ``--agent`` produces: ``session_id`` NULL,
which is what makes every server-side site ask for expansion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from omnigent.cli import _preregister_agent
from omnigent.runtime import get_agent_cache, get_agent_store, get_artifact_store

pytestmark = pytest.mark.asyncio

VAR = "OMNIGENT_TEST_SESSION_CREATE_RUNNER_ONLY_TOKEN"


def _register(tmp_path: Path, name: str, *, env_expansion: str | None) -> str:
    config: dict[str, object] = {
        "spec_version": 1,
        "name": name,
        "executor": {"type": "omnigent", "config": {"harness": "claude-sdk"}},
        "tools": {
            "remote": {
                "type": "mcp",
                "url": "http://127.0.0.1:9/mcp/",
                "headers": {"Authorization": "Bearer ${" + VAR + "}"},
            }
        },
    }
    if env_expansion is not None:
        config["env_expansion"] = env_expansion
    root = tmp_path / name
    root.mkdir()
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    agent_id = _preregister_agent(root, get_agent_store(), get_artifact_store(), get_agent_cache())
    assert agent_id is not None
    return agent_id


async def test_a_runner_only_agent_creates_a_session_without_the_server_variable(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(VAR, raising=False)
    agent_id = _register(tmp_path, "runner-only-agent", env_expansion="runner")

    resp = await client.post("/v1/sessions", json={"agent_id": agent_id})
    assert resp.status_code == 201, resp.text


async def test_a_runner_only_agent_is_listed_without_the_server_variable(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The listing expanded too, and it fails silently rather than loudly.

    ``_to_agent_object`` swallows a spec load error and returns the agent with
    no harness and no MCP servers, so the agent still appears in the list and
    the damage is invisible. Assert on what that failure takes away.
    """
    monkeypatch.delenv(VAR, raising=False)
    _register(tmp_path, "runner-only-listed", env_expansion="runner")

    resp = await client.get("/v1/agents")
    assert resp.status_code == 200, resp.text
    (agent,) = [a for a in resp.json()["data"] if a["name"] == "runner-only-listed"]
    assert agent["harness"] is not None, agent
    assert [m["name"] for m in agent["mcp_servers"]] == ["remote"], agent
    assert agent["mcp_servers"][0]["headers"] == {"Authorization": "[REDACTED]"}


async def test_an_ordinary_operator_agent_is_still_refused_for_an_unset_variable(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control. Proves this path expands, and that the fix did not stop it for everyone."""
    monkeypatch.setenv(VAR, "present-at-registration")
    agent_id = _register(tmp_path, "ordinary-operator", env_expansion=None)
    get_agent_cache()._specs.clear()
    monkeypatch.delenv(VAR, raising=False)

    resp = await client.post("/v1/sessions", json={"agent_id": agent_id})
    assert resp.status_code == 400, resp.text
    assert "Unresolved environment variable" in resp.text


async def test_an_ordinary_operator_agent_still_expands_when_the_variable_is_set(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VAR, "server-value")
    agent_id = _register(tmp_path, "ordinary-operator-set", env_expansion=None)

    resp = await client.post("/v1/sessions", json={"agent_id": agent_id})
    assert resp.status_code == 201, resp.text
    agent = get_agent_store().get(agent_id)
    loaded = get_agent_cache().load(agent_id, agent.bundle_location, expand_env=True)
    (server,) = [s for s in loaded.spec.mcp_servers if s.name == "remote"]
    assert server.headers["Authorization"] == "Bearer server-value"
