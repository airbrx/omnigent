"""Eva's bundle, bindings and tool boundary.

Half of these break the rules on purpose. A boundary that always returned ALLOW
would pass every happy-path assertion here, so the denials are asserted by name.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from omnigent.airbrx.eva import config as eva_config
from omnigent.airbrx.eva.package import bundle_root, is_eva
from omnigent.airbrx.eva.policy import (
    BROWSER_SERVER,
    BROWSER_TOOLS,
    DISCOVERY_TOOLS,
    EVA_TOOLS,
    WITHHELD,
    linkedin_url_ok,
    tool_boundary,
)

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


def test_only_the_runner_expands_her_bundle() -> None:
    """Her bearer token is per host and must never be in the coordinator's env.

    Without ``env_expansion: runner`` every session create on the coordinator
    fails for an unset OUTREACH_MCP_TOKEN, which is what happened in production
    on 2026-09-24 once the stand-in values were removed.
    """
    assert _spec().env_expansion == "runner"


def test_the_coordinator_loads_her_without_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from omnigent.spec import load

    monkeypatch.delenv("OUTREACH_MCP_TOKEN", raising=False)
    monkeypatch.delenv("OUTREACH_MCP_URL", raising=False)
    spec = load(bundle_root(), expand_env=True, server_side=True)
    (server,) = [s for s in spec.mcp_servers if s.name == "outreach"]
    assert server.headers["Authorization"] == "Bearer ${OUTREACH_MCP_TOKEN}"


def test_eva_runs_on_the_meta_harness_with_no_model_pinned() -> None:
    """The point of the omni harness is that the coordinator's provider decides.

    A model named here would pin every rep to one model, which is what Abram
    asked not to happen. Iris pins `claude-sonnet-4-6`; Eva must not.
    """
    ex = _spec().executor
    assert ex.type == "omnigent"
    assert ex.model is None
    assert ex.config.get("smart_routing_harness") == "auto"


def test_no_local_tools_and_exactly_two_mcp_servers() -> None:
    """Zero local tools is why Eva needs no vendored archive or keychain apparatus.

    The second server, the browser, is a stdio MCP server the runner spawns on
    the execution host (2026-09-26). If this fails with a local tool, Iris's
    host apparatus (pat_ref, prepare_host, the launchd delta) comes back with it.
    """
    spec = _spec()
    assert spec.local_tools == []
    assert [s.name for s in spec.mcp_servers] == ["outreach", BROWSER_SERVER]


def _server(name: str):
    (server,) = [s for s in _spec().mcp_servers if s.name == name]
    return server


def test_the_spec_allow_list_matches_the_policy_allow_list() -> None:
    """Two expressions of one boundary, and nothing else asserts they agree.

    They are written in different files in different languages. This is the test
    that stops them drifting apart, which is the defect shape this repository
    has already recorded twice against literals spelled in two places.
    """
    assert set(_server("outreach").tools) == set(EVA_TOOLS)
    assert set(_server(BROWSER_SERVER).tools) == set(BROWSER_TOOLS)


def test_the_two_withheld_tools_are_in_neither_list() -> None:
    spec_tools = {t for server in _spec().mcp_servers for t in server.tools or ()}
    for name in ("approve_draft", "mark_sent"):
        assert name not in spec_tools
        assert name not in EVA_TOOLS
        assert name in WITHHELD


def test_instructions_carry_the_house_rules() -> None:
    text = (bundle_root() / "AGENTS.md").read_text()
    assert "never send" in text.lower()
    assert "em dash" in text.lower()


#: The tools behind the Scoreboard, LinkedIn and GTM plan pages (2026-09-26).
DASHBOARD_TOOLS = (
    "list_linkedin_posts",
    "record_linkedin_metrics",
    "list_plan_items",
    "record_plan_actual",
)


@pytest.mark.parametrize("name", DASHBOARD_TOOLS)
def test_a_dashboard_tool_is_allowed_and_her_instructions_say_how_to_use_it(name: str) -> None:
    """Instructions naming a tool the boundary denies would send her into a refusal."""
    assert name in EVA_TOOLS
    assert (
        tool_boundary({"type": "tool_call", "target": f"mcp__omnigent__{name}"})["result"]
        == "ALLOW"
    )
    assert f"`{name}`" in (bundle_root() / "AGENTS.md").read_text()


def test_her_instructions_answer_both_dashboard_asks() -> None:
    """The workspace sends these two sentences; AGENTS.md must say how to answer each."""
    text = (bundle_root() / "AGENTS.md").read_text()
    assert "Refresh the LinkedIn post stats" in text
    assert "Give me a read on the outreach scoreboard for" in text
    # No browser is the usual case, and the rep must hear it rather than get made-up numbers.
    assert "no browser tool" in text
    assert "paste" in text


# --------------------------------------------------------------------------
# The browser: approved 2026-09-26, declared in her bundle, fenced to linkedin.com
# --------------------------------------------------------------------------

#: Tools that are not hers under any arguments: other browsers, Playwright's
#: action tools, bare or wrongly namespaced browser names, and the ambient tools
#: a harness could hand her.
NOT_HERS = (
    "mcp__claude-in-chrome__navigate",
    "mcp__claude-in-chrome__computer",
    "mcp__claude-in-chrome__form_input",
    "mcp__claude-in-chrome__javascript_tool",
    "mcp__playwright__browser_navigate",
    "mcp__omnigent__playwright__browser_navigate",
    "mcp__omnigent__browser__browser_click",
    "mcp__omnigent__browser__browser_type",
    "mcp__omnigent__browser__browser_fill_form",
    "mcp__omnigent__browser__browser_press_key",
    "mcp__omnigent__browser__browser_evaluate",
    "mcp__omnigent__browser__browser_run_code_unsafe",
    "mcp__omnigent__browser__browser_file_upload",
    "mcp__omnigent__browser__browser_take_screenshot",
    "mcp__omnigent__browser__browser_tabs",
    "mcp__omnigent__browser__browser_handle_dialog",
    "mcp__omnigent__browser__browser_select_option",
    "mcp__omnigent__browser__browser_network_requests",
    "mcp__omnigent__browser__list_pool",
    "mcp__omnigent__outreach__browser_navigate",
    "browser_navigate",
    "browser_type",
    "Bash",
    "Write",
    "Edit",
    "WebFetch",
    "mcp__omnigent__sys_os_shell",
    "mcp__claude_ai_Gmail__send_message",
    "mcp__claude_ai_Slack__slack_send_message",
)

_LINKEDIN = "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000000/"


def _call(target: str, arguments: object = None) -> dict:
    return tool_boundary(
        {"type": "tool_call", "target": target, "data": {"name": target, "arguments": arguments}}
    )


def _framework_tool_names() -> list[str]:
    """The always-on tools ``ToolManager`` registers for her real spec.

    ``tools.builtins: []`` does not remove these, and the embedded browser's
    bare ``browser_navigate``/``browser_click``/``browser_type`` are among
    them. Hers arrive only as ``browser__<tool>``. MCP tools are registered
    only by ``start()``, so an unstarted manager lists exactly the framework set.
    """
    from omnigent.tools.manager import ToolManager

    names = {schema["function"]["name"] for schema in ToolManager(_spec()).get_tool_schemas()}
    hers = EVA_TOOLS | DISCOVERY_TOOLS | {f"{BROWSER_SERVER}__{t}" for t in BROWSER_TOOLS}
    return sorted(names - hers)


def test_every_framework_tool_is_denied() -> None:
    """Carrying a linkedin.com URL too, so the embedded browser gets no pass."""
    names = _framework_tool_names()
    assert names
    assert {"sys_add_policy", "sys_scheduled_task_create"} <= set(names)
    for name in names:
        for target in (name, f"mcp__omnigent__{name}"):
            assert _call(target)["result"] == "DENY", target
            assert _call(target, {"url": _LINKEDIN})["result"] == "DENY", target


@pytest.mark.parametrize("name", NOT_HERS)
def test_no_other_browser_action_shell_file_or_server_tool_is_allowed(name: str) -> None:
    """Even carrying a linkedin.com URL, which is what a lured call would carry."""
    assert tool_boundary({"type": "tool_call", "target": name})["result"] == "DENY"
    assert _call(name, {"url": _LINKEDIN})["result"] == "DENY"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/",
        "https://www.linkedin.com/feed/",
        _LINKEDIN,
        "https://www.linkedin.com/analytics/post-summary/urn:li:activity:7000000000000000000/",
        "https://www.linkedin.com/company/airbrx/admin/analytics/updates/",
    ],
)
@pytest.mark.parametrize("prefix", ["mcp__omnigent__browser__", "browser__"])
def test_navigate_to_linkedin_is_allowed(prefix: str, url: str) -> None:
    assert _call(f"{prefix}browser_navigate", {"url": url})["result"] == "ALLOW"


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        42,
        "https://www.linkedin.com",  # no path: the prefix is the rule, slash and all
        "http://www.linkedin.com/feed/",
        "https://linkedin.com/feed/",
        "https://lnkd.in/abc",
        "https://www.linkedin.com.evil.example/",
        "https://www.linkedin.com@evil.example/",
        "https://www.linkedin.com:8443/feed/",
        "https://evil.example/https://www.linkedin.com/",
        "https://www.linkedin.com/\\evil.example",
        "https://www.linkedin.com/ feed",
        "https://www.linkedin.com/\nfeed",
        "javascript:alert(1)//https://www.linkedin.com/",
        "file:///etc/passwd",
        "about:blank",
        "https://www.google.com/search?q=https://www.linkedin.com/",
    ],
)
def test_navigate_anywhere_else_is_denied(url: object) -> None:
    out = _call("mcp__omnigent__browser__browser_navigate", {"url": url})
    assert out["result"] == "DENY"
    assert "linkedin.com" in out["reason"]
    assert linkedin_url_ok(url) is False


def test_navigate_with_no_or_unreadable_arguments_is_denied() -> None:
    target = "mcp__omnigent__browser__browser_navigate"
    assert tool_boundary({"type": "tool_call", "target": target})["result"] == "DENY"
    assert _call(target, {})["result"] == "DENY"
    assert _call(target, "not json")["result"] == "DENY"
    assert _call(target, ["https://www.linkedin.com/"])["result"] == "DENY"
    assert tool_boundary({"type": "tool_call", "target": target, "data": "x"})["result"] == "DENY"


def test_arguments_are_read_in_every_shape_the_engines_send() -> None:
    """``{"name", "arguments"}`` (engine), JSON text, and ``{"tool", "args"}`` (inner)."""
    target = "browser__browser_navigate"
    good, bad = {"url": _LINKEDIN}, {"url": "https://evil.example/"}
    assert _call(target, good)["result"] == "ALLOW"
    assert _call(target, json.dumps(good))["result"] == "ALLOW"
    assert _call(target, json.dumps(bad))["result"] == "DENY"
    inner = {"type": "tool_call", "target": target, "data": {"tool": target, "args": good}}
    assert tool_boundary(inner)["result"] == "ALLOW"
    inner["data"]["args"] = bad
    assert tool_boundary(inner)["result"] == "DENY"


def test_an_extra_navigate_argument_is_denied() -> None:
    out = _call("browser__browser_navigate", {"url": _LINKEDIN, "waitUntil": "load"})
    assert out["result"] == "DENY"


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("browser_snapshot", {}),
        ("browser_snapshot", None),
        ("browser_snapshot", {"depth": 8}),
        ("browser_snapshot", {"target": "e12"}),
        ("browser_wait_for", {"text": "Impressions"}),
        ("browser_wait_for", {"time": 3}),
        ("browser_wait_for", {"textGone": "Loading"}),
        ("browser_navigate_back", {}),
        ("browser_close", {}),
    ],
)
def test_the_read_tools_are_allowed(tool: str, arguments: object) -> None:
    assert _call(f"mcp__omnigent__browser__{tool}", arguments)["result"] == "ALLOW"


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        # A snapshot to a file writes to the host; that is not reading.
        ("browser_snapshot", {"filename": "../../.ssh/authorized_keys"}),
        ("browser_snapshot", {"filename": "page.yml"}),
        ("browser_wait_for", {"time": 600}),
        ("browser_wait_for", {"time": 0}),
        ("browser_wait_for", {"time": -1}),
        ("browser_wait_for", {"time": True}),
        ("browser_wait_for", {"time": "5"}),
        ("browser_navigate_back", {"url": _LINKEDIN}),
        ("browser_close", {"force": True}),
    ],
)
def test_a_read_tool_with_other_arguments_is_denied(tool: str, arguments: object) -> None:
    assert _call(f"mcp__omnigent__browser__{tool}", arguments)["result"] == "DENY"


def test_the_outreach_server_carries_no_browser_and_the_browser_no_action() -> None:
    """Each server carries only its own tools, and the browser only read tools."""
    broader = re.compile(r"browser|chrome|navigate|click|shell|bash|exec|write_file|upload", re.I)
    assert not [t for t in _server("outreach").tools if broader.search(t)]
    assert not [t for t in EVA_TOOLS if broader.search(t)]
    action = re.compile(
        r"click|type|press|key|fill|select|drag|drop|hover|evaluate|run_code|upload"
        r"|screenshot|pdf|tabs|cookie|storage|network|route|dialog|resize|webmcp|install",
        re.I,
    )
    assert not [t for t in _server(BROWSER_SERVER).tools if action.search(t)]


def test_the_browser_server_is_fenced_at_launch() -> None:
    """The launch flags are the third and fourth fences; pin them.

    Its own profile, headless, requests only to linkedin.com and licdn.com,
    no page-registered tools, no extra capabilities, and no way onto the
    operator's own Chrome (``--extension``, ``--cdp-endpoint``).
    """
    server = _server(BROWSER_SERVER)
    assert server.transport == "stdio"
    assert server.command == "/bin/sh"
    assert server.args[0] == "-c"
    line = server.args[1]
    assert '--user-data-dir "$HOME/.eva-linkedin-profile"' in line
    assert "/.eva-playwright/node_modules/.bin/playwright-mcp" in line
    assert "--browser chrome" in line
    assert "--headless" in line
    assert "--sandbox" in line and "--no-sandbox" not in line
    assert '--allowed-origins "https://www.linkedin.com;*.licdn.com"' in line
    assert "--no-webmcp" in line
    for absent in (
        "--caps",
        "--extension",
        "--cdp-endpoint",
        "--isolated",
        "--port",
        "--allow-unrestricted-file-access",
        "--storage-state",
        "--secrets",
    ):
        assert absent not in line, absent


def test_her_instructions_carry_the_browser_rules() -> None:
    """Abram's rules for the browser, in the file that tells her."""
    text = (bundle_root() / "AGENTS.md").read_text()
    rules = text[text.index("### The browser rules") :]
    rules = rules[: rules.index("\n### ", 1)]
    lower = rules.lower()
    assert "https://www.linkedin.com/" in rules
    assert "airbrx's and the founders' own linkedin posts" in lower
    for never in ("post", "comment", "react", "message", "connect", "follow", "edit a profile"):
        assert never in lower, never
    assert "never visit another site" in lower
    assert "sign in" in lower and "stop" in lower
    assert "record_linkedin_metrics" in rules and "once" in lower
    assert "never" in lower and "estimate" in lower


def test_the_paste_in_route_stays_for_a_refusal_or_a_sign_in() -> None:
    text = (bundle_root() / "AGENTS.md").read_text()
    assert "If you have no browser tool, or a browser call is refused" in text
    assert "paste each post's numbers" in text
    assert "asks for a sign-in" in text


@pytest.mark.parametrize("tool", sorted(BROWSER_TOOLS))
def test_her_instructions_name_each_browser_tool(tool: str) -> None:
    assert f"`{tool}`" in (bundle_root() / "AGENTS.md").read_text()


def test_her_instructions_say_to_snapshot_and_never_to_sign_in() -> None:
    text = (bundle_root() / "AGENTS.md").read_text()
    assert "call `browser_snapshot` after" in text
    for marker in ("/login", "/authwall", "/checkpoint"):
        assert marker in text
    assert "never ask the rep for a password" in text


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
    assert b.mcp_url() == "https://eva.airbrx.ai/mcp/"
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
                    # "workspace" became a real field for the workspace; a
                    # misspelling of it is the realistic typo now.
                    "worksapce": "/some/path",
                }
            ],
        ),
    )
    with pytest.raises(ValueError, match="worksapce"):
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


def test_a_relative_workspace_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Session create needs an absolute runner directory; say so at startup."""
    monkeypatch.setenv(
        "OMNIGENT_EVA_CONFIG",
        _write(
            tmp_path,
            [
                {
                    "users": ["a@airbrx.com"],
                    "host_id": "882128953d2a4e178ddbd48d70b298a1",
                    "base_url": "http://127.0.0.1:8000",
                    "token_ref": "keychain:eva-outreach-token",
                    "workspace": "relative/dir",
                }
            ],
        ),
    )
    with pytest.raises(ValueError, match="absolute"):
        eva_config.bindings()
