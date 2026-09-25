"""A coordinator naming a secret is a request, and the host decides.

Without the allowlist a coordinator could ask a host to read any entry in its
keychain and put it in the environment of a process that runs an agent, and that
agent can print its own environment. That is the thing
``_RUNNER_ENV_ALLOWLIST`` exists to prevent by a different route, so a new route
to it needs the same gate.
"""

from __future__ import annotations

import pytest

from omnigent.host.connect import (
    HOST_SECRET_REFS_ENV_VAR,
    SecretRefNotAllowed,
    allowed_secret_refs,
    resolve_agent_secret_env,
)

REF = "keychain:eva-outreach-token"


def test_a_host_that_allows_nothing_resolves_nothing() -> None:
    """The default. An unconfigured host must not hand out secrets."""
    assert allowed_secret_refs({}) == frozenset()

    with pytest.raises(SecretRefNotAllowed, match=HOST_SECRET_REFS_ENV_VAR):
        resolve_agent_secret_env({"OUTREACH_MCP_TOKEN": REF}, base_env={})


def test_no_references_asked_for_is_not_an_error() -> None:
    """Most launches carry none, and must not pay for the allowlist."""
    assert resolve_agent_secret_env({}, base_env={}) == {}


def test_the_allowlist_is_exact_rather_than_a_prefix() -> None:
    """``keychain:`` in the list must not permit every keychain entry."""
    base = {HOST_SECRET_REFS_ENV_VAR: "keychain:"}

    with pytest.raises(SecretRefNotAllowed):
        resolve_agent_secret_env({"T": REF}, base_env=base)


def test_whitespace_and_empties_in_the_list_are_tolerated() -> None:
    base = {HOST_SECRET_REFS_ENV_VAR: f" {REF} , , keychain:other "}

    assert allowed_secret_refs(base) == frozenset({REF, "keychain:other"})


def test_an_allowed_reference_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ``env:`` reference is the shape that needs no keychain to test."""
    monkeypatch.setenv("A_TEST_TOKEN", "tok-live")
    base = {HOST_SECRET_REFS_ENV_VAR: "env:A_TEST_TOKEN"}

    got = resolve_agent_secret_env({"OUTREACH_MCP_TOKEN": "env:A_TEST_TOKEN"}, base_env=base)

    assert got == {"OUTREACH_MCP_TOKEN": "tok-live"}


def test_allowed_but_unresolvable_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused launch beats a runner missing one variable.

    A runner that starts without its credential fails its first call with a
    401, which reads as an authentication problem and sends whoever is looking
    at the token rather than at the host.
    """
    monkeypatch.delenv("A_MISSING_TOKEN", raising=False)
    base = {HOST_SECRET_REFS_ENV_VAR: "env:A_MISSING_TOKEN"}

    with pytest.raises(SecretRefNotAllowed, match="did not resolve"):
        resolve_agent_secret_env({"T": "env:A_MISSING_TOKEN"}, base_env=base)
