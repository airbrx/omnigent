"""Eva's bundle, bindings and tool boundary.

Half of these break the rules on purpose. A boundary that always returned ALLOW
would pass every happy-path assertion here, so the denials are asserted by name.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from omnigent.airbrx.eva import config as eva_config
from omnigent.airbrx.eva.package import bundle_root, is_eva
from omnigent.airbrx.eva.policy import EVA_TOOLS, WITHHELD, tool_boundary

# --------------------------------------------------------------------------
# The bundle
# --------------------------------------------------------------------------


def _spec():
    from omnigent.spec.parser import parse

    os.environ.setdefault("OUTREACH_MCP_URL", "https://eva.example.invalid/mcp")
    os.environ.setdefault("OUTREACH_MCP_TOKEN", "test-token-not-a-secret")
    return parse(bundle_root())


def test_the_bundle_parses_and_is_named_eva() -> None:
    assert _spec().name == "eva"


def test_eva_runs_on_the_meta_harness_with_no_model_pinned() -> None:
    """The point of the omni harness is that the coordinator's provider decides.

    A model named here would pin every rep to one model, which is what Abram
    asked not to happen. Iris pins `claude-sonnet-4-6`; Eva must not.
    """
    ex = _spec().executor
    assert ex.type == "omnigent"
    assert ex.model is None
    assert ex.config.get("smart_routing_harness") == "auto"


def test_every_tool_is_remote_so_no_execution_host_is_needed() -> None:
    """Zero local tools is the reason Eva needs no host binding or keychain.

    If this ever fails, a local tool has been added and the whole of Iris's host
    apparatus (pat_ref, prepare_host, the launchd delta) comes back with it.
    """
    spec = _spec()
    assert spec.local_tools == []
    assert len(spec.mcp_servers) == 1


def test_the_spec_allow_list_matches_the_policy_allow_list() -> None:
    """Two expressions of one boundary, and nothing else asserts they agree.

    They are written in different files in different languages. This is the test
    that stops them drifting apart, which is the defect shape this repository
    has already recorded twice against literals spelled in two places.
    """
    spec_tools = set(_spec().mcp_servers[0].tools)
    assert spec_tools == set(EVA_TOOLS)


def test_the_two_withheld_tools_are_in_neither_list() -> None:
    spec_tools = set(_spec().mcp_servers[0].tools)
    for name in ("approve_draft", "mark_sent"):
        assert name not in spec_tools
        assert name not in EVA_TOOLS
        assert name in WITHHELD


def test_instructions_carry_the_house_rules() -> None:
    text = (bundle_root() / "AGENTS.md").read_text()
    assert "never send" in text.lower()
    assert "em dash" in text.lower()


def test_no_em_dash_in_eva_s_own_instructions() -> None:
    """The rule Eva is told to follow, applied to the file that tells her."""
    assert "—" not in (bundle_root() / "AGENTS.md").read_text()


# --------------------------------------------------------------------------
# The boundary. The denials are the point.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(EVA_TOOLS))
def test_every_allowed_tool_is_allowed(name: str) -> None:
    assert tool_boundary({"type": "tool_call", "target": name})["result"] == "ALLOW"


@pytest.mark.parametrize("name", sorted(WITHHELD))
def test_the_withheld_tools_are_denied_with_a_reason(name: str) -> None:
    out = tool_boundary({"type": "tool_call", "target": name})
    assert out["result"] == "DENY"
    assert name in out["reason"]


def test_denial_survives_the_mcp_prefix() -> None:
    """A hosted turn sees `mcp__omnigent__mark_sent`, not `mark_sent`.

    Without the prefix strip the boundary would pass the withheld tool straight
    through, which is the failure mode of checking a name you never normalised.
    """
    for target in ("mcp__omnigent__mark_sent", "outreach__mark_sent"):
        assert tool_boundary({"type": "tool_call", "target": target})["result"] == "DENY"


def test_an_unknown_tool_is_denied() -> None:
    assert tool_boundary({"type": "tool_call", "target": "Bash"})["result"] == "DENY"
    assert tool_boundary({"type": "tool_call", "target": ""})["result"] == "DENY"


def test_discovery_is_allowed_because_denying_it_breaks_the_agent() -> None:
    assert tool_boundary({"type": "tool_call", "target": "ToolSearch"})["result"] == "ALLOW"


def test_non_tool_events_pass_through() -> None:
    assert tool_boundary({"type": "message"})["result"] == "ALLOW"


def test_is_eva_does_not_raise_on_a_spec_without_a_name() -> None:
    """The AttributeError that took down dispatch for every agent, not just one."""

    class Stub:
        pass

    assert is_eva(Stub()) is False
    assert is_eva(None) is False


# --------------------------------------------------------------------------
# Bindings
# --------------------------------------------------------------------------


def _write(tmp_path: Path, rows: object) -> str:
    p = tmp_path / "eva.json"
    p.write_text(json.dumps(rows))
    return str(p)


def test_no_config_means_eva_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIGENT_EVA_CONFIG", raising=False)
    assert eva_config.bindings() == ()


def test_a_binding_parses_and_builds_its_mcp_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_EVA_CONFIG",
        _write(
            tmp_path,
            [
                {
                    "users": ["aerickson@airbrx.com"],
                    "host_id": "882128953d2a4e178ddbd48d70b298a1",
                    "base_url": "https://eva.airbrx.ai/",
                    "token_ref": "keychain:eva-outreach-token",
                }
            ],
        ),
    )
    (b,) = eva_config.bindings()
    assert b.mcp_url() == "https://eva.airbrx.ai/mcp"
    assert b.fixture is False


def test_an_unknown_key_stops_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in an authorization file must fail loudly, not silently widen it."""
    monkeypatch.setenv(
        "OMNIGENT_EVA_CONFIG",
        _write(
            tmp_path,
            [
                {
                    "users": ["a@airbrx.com"],
                    "host_id": "882128953d2a4e178ddbd48d70b298a1",
                    "base_url": "https://eva.airbrx.ai",
                    "token_ref": "env:X",
                    "workspace": "/some/path",
                }
            ],
        ),
    )
    with pytest.raises(ValueError, match="workspace"):
        eva_config.bindings()


def test_a_binding_with_no_users_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_EVA_CONFIG",
        _write(
            tmp_path,
            [
                {
                    "users": [],
                    "host_id": "882128953d2a4e178ddbd48d70b298a1",
                    "base_url": "https://e.ai",
                    "token_ref": "env:X",
                }
            ],
        ),
    )
    with pytest.raises(ValueError, match="nobody"):
        eva_config.bindings()


def test_a_live_binding_without_a_token_ref_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_EVA_CONFIG",
        _write(
            tmp_path,
            [
                {
                    "users": ["a@airbrx.com"],
                    "host_id": "882128953d2a4e178ddbd48d70b298a1",
                    "base_url": "https://e.ai",
                }
            ],
        ),
    )
    with pytest.raises(ValueError, match="token_ref"):
        eva_config.bindings()


def test_a_fixture_binding_may_omit_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_EVA_CONFIG",
        _write(
            tmp_path,
            [
                {
                    "users": ["a@airbrx.com"],
                    "host_id": "882128953d2a4e178ddbd48d70b298a1",
                    "base_url": "https://fixture.invalid",
                    "fixture": True,
                    "label": "fixture",
                }
            ],
        ),
    )
    (b,) = eva_config.bindings()
    assert b.fixture is True


def test_a_relative_base_url_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "OMNIGENT_EVA_CONFIG",
        _write(
            tmp_path,
            [
                {
                    "users": ["a@x.com"],
                    "host_id": "882128953d2a4e178ddbd48d70b298a1",
                    "base_url": "eva.airbrx.ai",
                    "token_ref": "e:X",
                }
            ],
        ),
    )
    with pytest.raises(ValueError, match="absolute"):
        eva_config.bindings()


def test_binding_for_refuses_to_guess_between_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exactly one match, or nothing. Two bindings are ambiguous, not redundant."""
    monkeypatch.setenv(
        "OMNIGENT_EVA_CONFIG",
        _write(
            tmp_path,
            [
                {
                    "users": ["a@x.com"],
                    "host_id": "882128953d2a4e178ddbd48d70b298a1",
                    "base_url": "https://one.invalid",
                    "token_ref": "env:A",
                    "label": "one",
                },
                {
                    "users": ["a@x.com"],
                    "host_id": "882128953d2a4e178ddbd48d70b298a1",
                    "base_url": "https://two.invalid",
                    "token_ref": "env:B",
                    "label": "two",
                },
            ],
        ),
    )
    assert eva_config.binding_for("a@x.com") is None
    assert eva_config.binding_for("a@x.com", label="two").base_url == "https://two.invalid"
    assert eva_config.binding_for("nobody@x.com") is None


def test_duplicate_labels_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "OMNIGENT_EVA_CONFIG",
        _write(
            tmp_path,
            [
                {
                    "users": ["a@x.com"],
                    "host_id": "882128953d2a4e178ddbd48d70b298a1",
                    "base_url": "https://a.invalid",
                    "token_ref": "env:A",
                },
                {
                    "users": ["b@x.com"],
                    "host_id": "882128953d2a4e178ddbd48d70b298a1",
                    "base_url": "https://b.invalid",
                    "token_ref": "env:B",
                },
            ],
        ),
    )
    with pytest.raises(ValueError, match="distinct labels"):
        eva_config.bindings()
