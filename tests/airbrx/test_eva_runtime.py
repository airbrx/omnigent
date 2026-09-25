"""The link between an Eva binding and the environment her MCP client expands.

This module exists because that link was missing. `token_ref` was parsed,
validated, stored and reported, and nothing ever resolved it, so a bound Eva
reached her tools with a literal `${OUTREACH_MCP_TOKEN}` in the Authorization
header and every call answered 401. Iris had the mechanism
(`iris/runtime.py` resolving `pat_ref` into a turn's environment); Eva had no
runtime module at all.

The tests below pin the two properties that make the fix safe to wire into a
runner launch: the mapping is complete or it raises, and it carries nothing
but the two variables the bundle names.
"""

from __future__ import annotations

import json

import pytest

from omnigent.airbrx.eva.config import Binding
from omnigent.airbrx.eva.runtime import (
    OUTREACH_TOKEN_VAR,
    OUTREACH_URL_VAR,
    launch_env,
    session_env,
)


def _binding(**over: object) -> Binding:
    base: dict[str, object] = {
        "users": ("aerickson@airbrx.com",),
        "host_id": "882128953d2a4e178ddbd48d70b298a1",
        "base_url": "http://127.0.0.1:8000",
        "token_ref": "env:TEST_OUTREACH_TOKEN",
        "label": "live",
        "fixture": False,
    }
    base.update(over)
    return Binding(**base)  # type: ignore[arg-type]


def test_the_url_keeps_the_trailing_slash_because_mcp_redirects_without_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/mcp` answers 307 and a POST that does not follow it looks like auth.

    The outreach app mounts the streamable HTTP transport at `/mcp/`. An hour
    was lost to this once already, diagnosed as a token problem because the
    symptom is a failed call on a correct credential.
    """
    monkeypatch.setenv("TEST_OUTREACH_TOKEN", "tok-live")
    env = session_env(_binding())
    assert env[OUTREACH_URL_VAR] == "http://127.0.0.1:8000/mcp/"


def test_a_base_url_with_its_own_trailing_slash_does_not_get_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_OUTREACH_TOKEN", "tok-live")
    env = session_env(_binding(base_url="http://127.0.0.1:8000/"))
    assert env[OUTREACH_URL_VAR] == "http://127.0.0.1:8000/mcp/"


def test_the_token_is_resolved_from_the_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_OUTREACH_TOKEN", "tok-live")
    env = session_env(_binding())
    assert env[OUTREACH_TOKEN_VAR] == "tok-live"


def test_an_unresolvable_reference_raises_rather_than_returning_a_url_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half an environment is worse than none.

    With the URL set and the token missing, the bundle expands, the client
    connects, and the server answers 401. The operator then debugs a token
    they believe is configured. Failing at the point the reference cannot be
    resolved names the actual problem.
    """
    monkeypatch.delenv("TEST_OUTREACH_TOKEN", raising=False)
    with pytest.raises(Exception) as caught:
        session_env(_binding())
    assert "TEST_OUTREACH_TOKEN" in str(caught.value)


def test_it_carries_the_two_variables_the_bundle_names_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This mapping is layered onto a runner's environment.

    Anything else it carried would be an unreviewed addition to a process that
    runs a rep's turn, so the key set is pinned rather than merely checked for
    the two that matter.
    """
    monkeypatch.setenv("TEST_OUTREACH_TOKEN", "tok-live")
    assert set(session_env(_binding())) == {OUTREACH_URL_VAR, OUTREACH_TOKEN_VAR}


def test_a_fixture_binding_gets_a_url_and_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fixture binding names no secret, so there is nothing to resolve.

    It still gets the URL: an acceptance run points at a stand-in app, and the
    absence of a token is the thing being exercised rather than an error.
    """
    monkeypatch.delenv("TEST_OUTREACH_TOKEN", raising=False)
    env = session_env(_binding(fixture=True, token_ref=""))
    assert env == {OUTREACH_URL_VAR: "http://127.0.0.1:8000/mcp/"}


def test_a_non_fixture_binding_with_no_token_ref_is_refused() -> None:
    """The one case that must not degrade quietly.

    A live binding whose `token_ref` is empty would otherwise produce the
    fixture shape: a URL, no token, and an agent in front of a real app with
    no credential. That is a configuration mistake and it should stop here.
    """
    with pytest.raises(ValueError, match="token_ref"):
        session_env(_binding(token_ref=""))


def test_a_binding_without_a_host_id_is_resolved_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A coordinator-local binding has no execution host and still needs this.

    When the outreach app runs beside the coordinator, the loopback is the
    coordinator's own and no host is named. The environment it needs is
    identical; only who builds it changes.
    """
    monkeypatch.setenv("TEST_OUTREACH_TOKEN", "tok-live")
    env = session_env(_binding(host_id=""))
    assert env[OUTREACH_TOKEN_VAR] == "tok-live"
    assert env[OUTREACH_URL_VAR] == "http://127.0.0.1:8000/mcp/"


def test_launch_env_hands_over_the_reference_and_not_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coordinator must never hold Eva's bearer token.

    This is the difference between :func:`launch_env` and :func:`session_env`
    and the reason both exist: one resolves here, the other defers to the host
    that actually has the secret.
    """
    monkeypatch.setenv(
        "OMNIGENT_EVA_CONFIG_JSON",
        json.dumps(
            [
                {
                    "label": "live",
                    "users": ["aerickson@airbrx.com"],
                    "base_url": "http://127.0.0.1:8000",
                    "host_id": "882128953d2a4e178ddbd48d70b298a1",
                    "token_ref": "keychain:eva-outreach-token",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "omnigent.airbrx.eva.runtime.bindings",
        lambda: (_binding(),),
    )

    got = launch_env("eva", "aerickson@airbrx.com")

    assert got is not None
    assert got.env == {OUTREACH_URL_VAR: "http://127.0.0.1:8000/mcp/"}
    assert got.secret_refs == {OUTREACH_TOKEN_VAR: "env:TEST_OUTREACH_TOKEN"}
    # The token value appears nowhere in what crosses the wire.
    assert "tok-live" not in str(got)


def test_launch_env_declines_an_agent_that_is_not_eva() -> None:
    assert launch_env("iris", "aerickson@airbrx.com") is None


def test_launch_env_declines_an_anonymous_caller() -> None:
    assert launch_env("eva", None) is None


def test_launch_env_refuses_to_guess_between_two_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two bindings for one user is what readiness answers with a 404.

    Picking one here would silently send a rep's turn at the wrong app, which
    is worse than a session that fails to launch and says why.
    """
    monkeypatch.setattr(
        "omnigent.airbrx.eva.runtime.bindings",
        lambda: (_binding(label="a"), _binding(label="b")),
    )

    assert launch_env("eva", "aerickson@airbrx.com") is None


def test_launch_env_for_a_fixture_binding_carries_no_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "omnigent.airbrx.eva.runtime.bindings",
        lambda: (_binding(fixture=True, token_ref=""),),
    )

    got = launch_env("eva", "aerickson@airbrx.com")

    assert got is not None
    assert got.secret_refs == {}
