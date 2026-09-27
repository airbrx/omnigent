"""Tally's bundle, her tool boundary, and her configuration.

Three statements of one allow list have to agree: the bundle's
``tools.portal.tools``, ``policy.TALLY_TOOLS``, and the workspace's check on a
finished turn. A fourth tool arriving in one place and not the others is the
failure this module exists to catch.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from omnigent.airbrx.tally.config import Binding, binding_for, bindings
from omnigent.airbrx.tally.package import bundle_root, is_tally, portrait_path
from omnigent.airbrx.tally.policy import (
    DISCOVERY_TOOLS,
    TALLY_TOOLS,
    WITHHELD,
    tool_boundary,
)
from omnigent.airbrx.tally.runtime import PORTAL_TOKEN_VAR, PORTAL_URL_VAR
from omnigent.airbrx.tally.workspace import _ALLOWED_IN_A_TURN
from omnigent.spec import load

FOUR = {"get_analytics_overview", "get_agent_policy", "get_sprint_board", "get_health"}


def _raw_config() -> dict[str, Any]:
    return yaml.safe_load((bundle_root() / "config.yaml").read_text())


# --------------------------------------------------------------------------
# The bundle
# --------------------------------------------------------------------------


def test_the_spec_tool_list_equals_the_policy_equals_the_workspace() -> None:
    spec = load(bundle_root(), expand_env=False)
    assert spec.name == "tally"
    (server,) = spec.mcp_servers
    assert server.name == "portal"
    assert set(server.tools or []) == FOUR
    assert len(server.tools or []) == 4
    assert TALLY_TOOLS == FOUR
    assert _ALLOWED_IN_A_TURN == TALLY_TOOLS | DISCOVERY_TOOLS
    assert {"ToolSearch"} == DISCOVERY_TOOLS


def test_every_tool_is_remote_and_there_is_no_ambient_surface() -> None:
    spec = load(bundle_root(), expand_env=False)
    assert spec.local_tools == []
    assert spec.skills_filter == "none"
    assert spec.spawn is False
    assert spec.env_expansion == "runner"
    raw = _raw_config()
    assert raw["tools"]["builtins"] == []
    assert raw["async"] is False
    assert raw["skills"] == "none"


def test_the_bundle_names_the_variables_the_runtime_sets() -> None:
    portal = _raw_config()["tools"]["portal"]
    assert portal["url"] == "${" + PORTAL_URL_VAR + "}"
    assert portal["headers"]["Authorization"] == "Bearer ${" + PORTAL_TOKEN_VAR + "}"


def test_the_bundle_validates_without_any_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """The coordinator learns her name with an empty environment, as with Eva."""
    monkeypatch.delenv(PORTAL_TOKEN_VAR, raising=False)
    monkeypatch.delenv(PORTAL_URL_VAR, raising=False)
    assert load(bundle_root(), expand_env=False).name == "tally"


def test_the_guardrail_points_at_this_policy() -> None:
    policy = _raw_config()["guardrails"]["policies"]["tally-tool-boundary"]
    assert policy["function"] == "omnigent.airbrx.tally.policy.tool_boundary"
    # YAML 1.1 reads a bare `on` key as True; Eva's bundle has the same line.
    assert policy.get("on", policy.get(True)) == ["tool_call"]


def test_agents_md_has_no_em_dash_and_says_she_is_read_only() -> None:
    text = (bundle_root() / "AGENTS.md").read_text()
    assert "—" not in text
    assert "## How you sound" in text
    assert "read-only" in text
    assert "never zero" in text.lower() or "missing data is never zero" in text.lower()


def test_the_bundle_config_has_no_em_dash() -> None:
    assert "—" not in (bundle_root() / "config.yaml").read_text()


def test_the_portrait_is_a_1254_square_png() -> None:
    data = portrait_path().read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    assert (width, height) == (1254, 1254)


# --------------------------------------------------------------------------
# is_tally
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("tally", True),
        ("tally (fork of tally)", True),
        ("tallyho", False),
        ("eva", False),
        ("iris", False),
        ("", False),
    ],
)
def test_is_tally(name: str, expected: bool) -> None:
    assert is_tally(SimpleNamespace(name=name)) is expected


def test_is_tally_reads_name_defensively() -> None:
    assert is_tally(object()) is False
    assert is_tally(None) is False


# --------------------------------------------------------------------------
# The boundary
# --------------------------------------------------------------------------


def _call(target: str, args: Any = None) -> dict[str, Any]:
    event: dict[str, Any] = {"type": "tool_call", "target": target}
    if args is not None:
        event["data"] = {"name": target, "arguments": args}
    return event


@pytest.mark.parametrize("prefix", ["", "portal__", "mcp__omnigent__", "mcp__omnigent__portal__"])
@pytest.mark.parametrize("tool", sorted(FOUR - {"get_agent_policy"}))
def test_each_read_is_allowed_with_and_without_prefixes(prefix: str, tool: str) -> None:
    assert tool_boundary(_call(prefix + tool))["result"] == "ALLOW"
    assert tool_boundary(_call(prefix + tool, {}))["result"] == "ALLOW"


@pytest.mark.parametrize("prefix", ["", "portal__", "mcp__omnigent__", "mcp__omnigent__portal__"])
@pytest.mark.parametrize("agent", ["iris", "eva"])
def test_agent_policy_is_allowed_for_iris_and_eva(prefix: str, agent: str) -> None:
    event = _call(prefix + "get_agent_policy", {"agent": agent})
    assert tool_boundary(event)["result"] == "ALLOW"
    # The engine's other argument shape, as a JSON string.
    event = {
        "type": "tool_call",
        "target": prefix + "get_agent_policy",
        "data": {"tool": "get_agent_policy", "args": json.dumps({"agent": agent})},
    }
    assert tool_boundary(event)["result"] == "ALLOW"


@pytest.mark.parametrize(
    "args",
    [
        None,
        {},
        {"agent": "tally"},
        {"agent": "IRIS"},
        {"agent": "iris", "tenant": "x"},
        "not json",
        ["iris"],
    ],
)
def test_agent_policy_with_a_bad_argument_is_denied(args: Any) -> None:
    event = _call("portal__get_agent_policy", args if args is not None else None)
    if args is None:
        event["data"] = {"name": "portal__get_agent_policy", "arguments": {}}
    assert tool_boundary(event)["result"] == "DENY"


def test_a_no_argument_read_may_not_carry_arguments() -> None:
    assert tool_boundary(_call("portal__get_health", {"verbose": True}))["result"] == "DENY"


@pytest.mark.parametrize(
    "target",
    [
        "mcp__claude_ai_Gmail__send_message",
        "mcp__claude_ai_Slack__slack_send_message",
        "mcp__claude_ai_Google_Drive__share_file",
        "portal__save_policy",
        "portal__publish_config_revision",
        "mcp__omnigent__portal__save_policy",
        "save_policy",
        "publish_config_revision",
        "update_tenant_rules",
        "Bash",
        "Write",
        "Edit",
        "WebFetch",
        "Skill",
        "load_skill",
        "mcp__omnigent__sys_os_shell",
        "outreach__list_pool",
        "browser__browser_navigate",
        "some_unknown_tool",
        "",
    ],
)
def test_everything_else_is_denied(target: str) -> None:
    assert tool_boundary(_call(target))["result"] == "DENY"


def test_another_servers_tool_does_not_become_hers_by_stripping() -> None:
    assert tool_boundary(_call("mcp__claude_ai_Slack__get_health"))["result"] == "DENY"


def test_a_withheld_tool_says_why() -> None:
    decision = tool_boundary(_call("portal__publish_config_revision"))
    assert decision["result"] == "DENY"
    assert "read-only" in decision["reason"]
    assert "separately approved" in decision["reason"]
    assert set(WITHHELD).isdisjoint(TALLY_TOOLS)


def test_tool_search_is_discovery() -> None:
    assert tool_boundary(_call("ToolSearch"))["result"] == "ALLOW"


@pytest.mark.parametrize("kind", ["llm_request", "llm_response", "message", None])
def test_non_tool_call_events_are_allowed(kind: str | None) -> None:
    assert tool_boundary({"type": kind, "target": "Bash"})["result"] == "ALLOW"


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

LIVE: dict[str, Any] = {
    "users": ["aerickson@airbrx.com"],
    "base_url": "http://127.0.0.1:4318",
    "token_ref": "env:AIRBRX_TALLY_MCP_TOKEN",
}


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: Any) -> None:
    path = tmp_path / "tally.json"
    path.write_text(json.dumps(rows))
    monkeypatch.setenv("OMNIGENT_TALLY_CONFIG", str(path))


def test_absent_config_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIGENT_TALLY_CONFIG", raising=False)
    assert bindings() == ()


def test_a_hostless_live_binding_parses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path, [LIVE])
    (b,) = bindings()
    assert b.host_id == ""
    assert b.label == "live"
    assert b.mcp_url() == "http://127.0.0.1:4318/mcp"
    assert b.is_host_local() is False


def test_the_mcp_url_has_no_trailing_slash() -> None:
    b = Binding(users=("u",), host_id="", base_url="http://127.0.0.1:4318/", token_ref="env:X")
    assert b.mcp_url() == "http://127.0.0.1:4318/mcp"


def test_a_loopback_on_a_named_host_is_host_local() -> None:
    b = Binding(users=("u",), host_id="h" * 32, base_url="http://127.0.0.1:4318", token_ref="x")
    assert b.is_host_local() is True


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({**LIVE, "token": "oops"}, "Unknown Tally binding setting"),
        ({**LIVE, "users": []}, "no users"),
        ({**LIVE, "base_url": "127.0.0.1:4318"}, "absolute http"),
        ({**LIVE, "token_ref": ""}, "token_ref"),
        ({**LIVE, "workspace": "relative/dir"}, "absolute path"),
    ],
)
def test_a_bad_binding_stops_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, row: dict[str, Any], message: str
) -> None:
    _configure(monkeypatch, tmp_path, [row])
    with pytest.raises(ValueError, match=message):
        bindings()


def test_the_config_must_be_a_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path, LIVE)
    with pytest.raises(ValueError, match="list"):
        bindings()


def test_labels_must_be_distinct(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path, [LIVE, LIVE])
    with pytest.raises(ValueError, match="distinct labels"):
        bindings()


def test_a_fixture_binding_needs_no_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path, [{**LIVE, "token_ref": "", "fixture": True}])
    (b,) = bindings()
    assert b.fixture is True


def test_binding_for_is_exactly_one_or_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path, [LIVE, {**LIVE, "label": "other"}])
    assert binding_for("aerickson@airbrx.com") is None
    assert binding_for("aerickson@airbrx.com", "other") is not None
    assert binding_for("nobody@airbrx.com") is None
