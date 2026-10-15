"""Per-session runner environment: the reference travels, the value does not."""

from __future__ import annotations

import pytest

from omnigent.runtime.launch_env import LaunchEnv, collect, register, reset


@pytest.fixture(autouse=True)
def _no_providers_leak() -> None:
    """Providers are process-global, so a test that registers one must not
    change what the next test sees."""
    reset()
    yield
    reset()


def test_nothing_registered_contributes_nothing() -> None:
    assert collect("eva", "abram@airbrx.com").is_empty()


def test_an_agent_with_no_name_collects_nothing() -> None:
    """A session whose agent cannot be resolved must not get another's values."""
    register(lambda name, user: LaunchEnv(env={"X": "1"}))

    assert collect(None, "abram@airbrx.com").is_empty()


def test_a_provider_that_declines_is_skipped() -> None:
    register(lambda name, user: None if name != "eva" else LaunchEnv(env={"X": "1"}))

    assert collect("iris", "abram@airbrx.com").is_empty()
    assert collect("eva", "abram@airbrx.com").env == {"X": "1"}


def test_values_and_references_stay_separate() -> None:
    """The split is the security property, not a formatting choice.

    Anything in ``env`` is sent to the host as written. Anything in
    ``secret_refs`` is a name the host resolves itself, so the value never
    crosses the wire.
    """
    register(
        lambda name, user: LaunchEnv(
            env={"URL": "http://127.0.0.1:8000/mcp/"},
            secret_refs={"TOKEN": "keychain:eva-outreach-token"},
        )
    )

    got = collect("eva", "abram@airbrx.com")

    assert got.env == {"URL": "http://127.0.0.1:8000/mcp/"}
    assert got.secret_refs == {"TOKEN": "keychain:eva-outreach-token"}


def test_two_providers_claiming_one_variable_is_an_error() -> None:
    """Letting one win would make the credential depend on import order.

    Which is the class of bug that is invisible in tests, reproducible only on
    the machine whose imports happen to differ, and reported as "it works here".
    """
    register(lambda name, user: LaunchEnv(secret_refs={"TOKEN": "keychain:a"}))
    register(lambda name, user: LaunchEnv(secret_refs={"TOKEN": "keychain:b"}))

    with pytest.raises(ValueError, match="both set 'TOKEN'"):
        collect("eva", "abram@airbrx.com")


def test_a_value_and_a_reference_cannot_collide_either() -> None:
    register(lambda name, user: LaunchEnv(env={"TOKEN": "literal"}))
    register(lambda name, user: LaunchEnv(secret_refs={"TOKEN": "keychain:a"}))

    with pytest.raises(ValueError, match="both set 'TOKEN'"):
        collect("eva", "abram@airbrx.com")
