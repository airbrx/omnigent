"""Registering Eva must not need her credentials, and must not be fatal.

Both properties are here because omnigent.airbrx.ai went down for them on the
same night, twice, with the same symptom and two different causes.

The second one is the interesting one. Eva's bundle names
``${OUTREACH_MCP_TOKEN}`` in an Authorization header, and that token lives on
the execution host, which is the entire point of holding a *reference* in the
binding rather than a value. The coordinator nevertheless expanded the bundle
while registering it, found the variable unset in its own environment, and
refused to start. A server that must hold a secret in order to learn an
agent's name has defeated the indirection it was built around.
"""

from __future__ import annotations

import pytest

from omnigent.airbrx.eva.package import bundle_root
from omnigent.spec import load


def test_evas_bundle_validates_without_any_of_her_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coordinator learns her name with an empty environment.

    This is the call `_preregister_agent` makes. With expansion on it raised
    on the unset variable and took the server with it.
    """
    monkeypatch.delenv("OUTREACH_MCP_TOKEN", raising=False)
    monkeypatch.delenv("OUTREACH_MCP_URL", raising=False)

    spec = load(bundle_root(), expand_env=False)

    assert spec.name == "eva"


def test_expanding_her_bundle_on_the_coordinator_still_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old behaviour, pinned so nobody restores it by accident.

    This is not a regression test for a bug in `load`. Refusing to expand an
    unset variable is correct and protects against a literal ``${VAR}`` going
    out as a bearer token. The defect was asking it to expand at all, on a
    machine that is never meant to hold the value.
    """
    monkeypatch.delenv("OUTREACH_MCP_TOKEN", raising=False)

    with pytest.raises(Exception) as caught:
        load(bundle_root(), expand_env=True)

    assert "OUTREACH_MCP_TOKEN" in str(caught.value)


def test_the_stored_artifact_is_the_unexpanded_bundle() -> None:
    """What the runner receives still carries the references.

    If registration ever stored an expanded bundle, a placeholder or a
    coordinator-side value would reach the runner and every tool call would
    401 on a credential nobody could see. The bundle on disk is the artifact,
    so this asserts the property at its source.
    """
    config = (bundle_root() / "config.yaml").read_text()

    assert "${OUTREACH_MCP_TOKEN}" in config
    assert "${OUTREACH_MCP_URL}" in config
