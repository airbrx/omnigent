"""Who a launch environment may be sent for: the host's owner, and nobody else.

A host secret reference resolves from the HOST's own store. On a host shared
with other users (``--shared``), sending one for a non-owner's session would
hand that user the host owner's credential. These pin the rule in
:func:`omnigent.server.routes._host_launch.launch_env_fields`.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.runtime import launch_env
from omnigent.runtime.launch_env import LaunchEnv
from omnigent.server.routes._host_launch import launch_env_fields

REF = {"OUTREACH_MCP_TOKEN": "keychain:eva-outreach-token"}
EMPTY = {"agent_env": None, "agent_secret_env": None}


class _Store:
    def get(self, agent_id: str) -> object:
        return SimpleNamespace(name="eva") if agent_id == "agent-eva" else None


@pytest.fixture(autouse=True)
def provider() -> Iterator[None]:
    launch_env.reset()
    launch_env.register(
        lambda name, user: LaunchEnv(secret_refs=dict(REF)) if name == "eva" and user else None
    )
    yield
    launch_env.reset()


def fields(**kw: Any) -> dict[str, Any]:
    return launch_env_fields(agent_id="agent-eva", agent_store=_Store(), **kw)


def test_the_host_owner_gets_the_reference() -> None:
    assert fields(user_id="a@x", host_owner="a@x")["agent_secret_env"] == REF


def test_a_non_owner_on_a_shared_host_gets_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """User B's session on A's shared host must not receive A's credential."""
    with caplog.at_level("WARNING"):
        out = fields(user_id="b@x", host_owner="a@x")
    assert out == EMPTY
    assert "withheld" in caplog.text


def test_an_unestablished_identity_gets_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """The relaunch path on a shared host cannot say whose session it is."""
    with caplog.at_level("WARNING"):
        out = fields(user_id="a@x", host_owner="a@x", identity_established=False)
    assert out == EMPTY
    assert "cannot establish" in caplog.text


def test_an_agent_with_no_provider_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        out = launch_env_fields(
            agent_id="other", user_id="b@x", host_owner="a@x", agent_store=_Store()
        )
    assert out == EMPTY
    assert "withheld" not in caplog.text


def test_an_uninitialized_runtime_collects_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    import omnigent.runtime as runtime

    def not_initialized() -> object:
        raise RuntimeError("runtime not initialized")

    monkeypatch.setattr(runtime, "get_agent_store", not_initialized)
    out = launch_env_fields(agent_id="agent-eva", user_id="a@x", host_owner="a@x")
    assert out == EMPTY


def test_a_failing_store_propagates_rather_than_launching_without_a_credential() -> None:
    class _Broken:
        def get(self, agent_id: str) -> object:
            raise ConnectionError("database gone")

    with pytest.raises(ConnectionError):
        launch_env_fields(
            agent_id="agent-eva", user_id="a@x", host_owner="a@x", agent_store=_Broken()
        )


def test_an_auth_disabled_single_user_server_is_allowed() -> None:
    """No user id means auth is off and there is exactly one user."""
    launch_env.reset()
    launch_env.register(
        lambda name, user: LaunchEnv(secret_refs=dict(REF)) if name == "eva" else None
    )
    assert fields(user_id=None, host_owner="local")["agent_secret_env"] == REF
