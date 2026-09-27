"""How Tally's token reference reaches her runner, and where it resolves.

Abram's decision is that Tally runs with no ``host_id``, next to the portal on
the coordinator. These tests pin what that means for the token, end to end
through the real functions:

1. ``launch_env`` hands over a *reference*, identical with or without a host.
2. ``launch_env_fields`` (the one function every host launch frame spreads)
   carries that reference only to the host owner's own runner.
3. The host resolves it with ``resolve_agent_secret_env``, only when the host
   owner allowed that exact reference in ``OMNIGENT_HOST_SECRET_REFS``.

What they do not show, because it is not true in this code: that a session
created with no host at all gets a runner. See docs/tally/RUNBOOK.md.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.airbrx.tally.config import Binding
from omnigent.airbrx.tally.runtime import (
    PORTAL_TOKEN_VAR,
    PORTAL_URL_VAR,
    launch_env,
    session_env,
)
from omnigent.host.connect import SecretRefNotAllowed, resolve_agent_secret_env
from omnigent.runtime import launch_env as launch_env_registry
from omnigent.server.routes._host_launch import launch_env_fields

USER = "aerickson@airbrx.com"
REF = "env:TEST_TALLY_MCP_TOKEN"


def _binding(**over: Any) -> Binding:
    base: dict[str, Any] = {
        "users": (USER,),
        "host_id": "",
        "base_url": "http://127.0.0.1:4318",
        "token_ref": REF,
    }
    base.update(over)
    return Binding(**base)


@pytest.fixture(autouse=True)
def _clean_registry():
    launch_env_registry.reset()
    yield
    launch_env_registry.reset()


def _bind(monkeypatch: pytest.MonkeyPatch, *rows: Binding) -> None:
    monkeypatch.setattr("omnigent.airbrx.tally.runtime.bindings", lambda: rows)


# --------------------------------------------------------------------------
# session_env and launch_env
# --------------------------------------------------------------------------


def test_session_env_resolves_the_reference_here(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TALLY_MCP_TOKEN", "tok-read")
    env = session_env(_binding())
    assert env == {PORTAL_URL_VAR: "http://127.0.0.1:4318/mcp", PORTAL_TOKEN_VAR: "tok-read"}


def test_session_env_raises_rather_than_returning_a_url_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TEST_TALLY_MCP_TOKEN", raising=False)
    with pytest.raises(Exception, match="TEST_TALLY_MCP_TOKEN"):
        session_env(_binding())


def test_a_fixture_binding_gets_a_url_and_no_token() -> None:
    assert session_env(_binding(fixture=True, token_ref="")) == {
        PORTAL_URL_VAR: "http://127.0.0.1:4318/mcp"
    }


def test_a_live_binding_with_no_reference_is_refused() -> None:
    with pytest.raises(ValueError, match="token_ref"):
        session_env(_binding(token_ref=""))


@pytest.mark.parametrize("host_id", ["", "a" * 32])
def test_launch_env_hands_over_the_reference_with_or_without_a_host(
    monkeypatch: pytest.MonkeyPatch, host_id: str
) -> None:
    monkeypatch.setenv("TEST_TALLY_MCP_TOKEN", "tok-read")
    _bind(monkeypatch, _binding(host_id=host_id))
    got = launch_env("tally", USER)
    assert got is not None
    assert got.env == {PORTAL_URL_VAR: "http://127.0.0.1:4318/mcp"}
    assert got.secret_refs == {PORTAL_TOKEN_VAR: REF}
    assert "tok-read" not in repr(got)


def test_launch_env_declines_other_agents_and_anonymous_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bind(monkeypatch, _binding())
    assert launch_env("eva", USER) is None
    assert launch_env("iris", USER) is None
    assert launch_env("tally", None) is None
    assert launch_env("tally", "someone-else@airbrx.com") is None


def test_launch_env_refuses_to_guess_between_two_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bind(monkeypatch, _binding(label="a"), _binding(label="b"))
    assert launch_env("tally", USER) is None


def test_eva_and_tally_providers_do_not_collide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both registered, as the server does; each answers only for its own agent."""
    from omnigent.airbrx.eva.runtime import launch_env as eva_launch_env

    _bind(monkeypatch, _binding())
    monkeypatch.setattr("omnigent.airbrx.eva.runtime.bindings", lambda: ())
    launch_env_registry.register(eva_launch_env)
    launch_env_registry.register(launch_env)
    got = launch_env_registry.collect("tally", USER)
    assert got.secret_refs == {PORTAL_TOKEN_VAR: REF}
    assert launch_env_registry.collect("eva", USER).is_empty()


# --------------------------------------------------------------------------
# The launch frame and the host
# --------------------------------------------------------------------------


class _Store:
    def get(self, agent_id: str) -> Any:
        return SimpleNamespace(name="tally") if agent_id == "agent-tally" else None


def test_the_launch_frame_carries_the_reference_to_the_host_owners_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bind(monkeypatch, _binding())
    launch_env_registry.register(launch_env)
    fields = launch_env_fields(
        agent_id="agent-tally", user_id=USER, host_owner=USER, agent_store=_Store()
    )
    assert fields == {
        "agent_env": {PORTAL_URL_VAR: "http://127.0.0.1:4318/mcp"},
        "agent_secret_env": {PORTAL_TOKEN_VAR: REF},
    }


def test_the_launch_frame_withholds_the_reference_from_another_owners_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bind(monkeypatch, _binding())
    launch_env_registry.register(launch_env)
    fields = launch_env_fields(
        agent_id="agent-tally",
        user_id=USER,
        host_owner="ops@airbrx.com",
        agent_store=_Store(),
    )
    assert fields == {"agent_env": None, "agent_secret_env": None}


def test_the_host_resolves_the_reference_only_when_it_allowed_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_TALLY_MCP_TOKEN", "tok-read")
    refs = {PORTAL_TOKEN_VAR: REF}
    allowed = {"OMNIGENT_HOST_SECRET_REFS": REF}
    assert resolve_agent_secret_env(refs, base_env=allowed) == {PORTAL_TOKEN_VAR: "tok-read"}
    with pytest.raises(SecretRefNotAllowed, match="OMNIGENT_HOST_SECRET_REFS"):
        resolve_agent_secret_env(refs, base_env={})
