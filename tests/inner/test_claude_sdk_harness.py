"""
Tests for the ``harness: claude-sdk`` wrap shape.

Does NOT exercise the real Claude SDK (no API key needed). Verifies:

- The wrap module exports :func:`create_app` and the registry
  resolves ``"claude-sdk"`` to it.
- ``create_app()`` returns a FastAPI app with the harness API
  subset routes wired up.
- The wrap reads its env-var config at executor construction
  time — verified by inspecting the lazy factory's behavior
  with mocked ``ClaudeSDKExecutor``.

End-to-end claude-sdk verification (real CLI, real API) lives in
the e2e suite and requires API keys.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from omnigent.inner import claude_sdk_harness
from omnigent.runtime.harnesses import _HARNESS_MODULES


def test_harness_module_registered_in_module_registry() -> None:
    """``"claude-sdk"`` resolves to the harness module path.

    Without this entry, the runner subprocess can't find the wrap
    when AP-side tries to spawn it.
    """
    assert _HARNESS_MODULES.get("claude-sdk") == "omnigent.inner.claude_sdk_harness"
    assert _HARNESS_MODULES.get("claude") == "omnigent.inner.claude_sdk_harness"


def test_create_app_returns_fastapi_with_required_routes() -> None:
    """``create_app()`` returns a FastAPI app exposing the harness API.

    Verifies the wrap successfully:
    - Imports the executor adapter + Claude SDK executor.
    - Builds the FastAPI app via ExecutorAdapter.build().
    - Mounts the standard harness routes.

    The actual ClaudeSDKExecutor is constructed lazily on the
    first turn (not at app build time), so this test passes
    without a real ``claude`` CLI on PATH.
    """
    app = claude_sdk_harness.create_app()
    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    # Session-keyed harness API: liveness probe + single
    # discriminated-event endpoint per §The Harness API Subset.
    assert "/health" in paths
    assert "/v1/sessions/{conversation_id}/events" in paths


def test_executor_factory_reads_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory passes env-var values through to ClaudeSDKExecutor.

    Locks in the v1 config-flow contract: env vars set in AP's
    process before spawning the subprocess (which inherits
    them) are how the wrap learns its config. Verifies model,
    databricks, profile, cwd, permission mode all thread
    through.
    """
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_MODEL", "test-model-id")
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_GATEWAY", "true")
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_DATABRICKS_PROFILE", "test-profile")
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_GATEWAY_HOST", "https://example.databricks.com")
    monkeypatch.setenv(
        "HARNESS_CLAUDE_SDK_GATEWAY_BASE_URL",
        "https://example.databricks.com/ai-gateway/anthropic",
    )
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_GATEWAY_AUTH_COMMAND", "printf token")
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_GATEWAY_AUTH_REFRESH_INTERVAL_MS", "900000")
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_CWD", "/tmp/test-cwd")
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_PERMISSION_MODE", "acceptEdits")

    captured: dict[str, Any] = {}

    def _fake_init(
        self: Any,
        *,
        cwd: str | None,
        os_env: Any,
        model: str | None,
        permission_mode: str,
        gateway: bool,
        databricks_profile: str | None,
        gateway_host: str | None,
        base_url_override: str | None,
        gateway_auth_command: str | None,
        gateway_auth_refresh_interval_ms: str | None,
        **_kwargs: Any,
    ) -> None:
        captured["cwd"] = cwd
        captured["os_env"] = os_env
        captured["model"] = model
        captured["permission_mode"] = permission_mode
        captured["gateway"] = gateway
        captured["databricks_profile"] = databricks_profile
        captured["gateway_host"] = gateway_host
        captured["base_url_override"] = base_url_override
        captured["gateway_auth_command"] = gateway_auth_command
        captured["gateway_auth_refresh_interval_ms"] = gateway_auth_refresh_interval_ms

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    # Each env var threaded through to the corresponding
    # constructor kwarg.
    assert captured["model"] == "test-model-id"
    assert captured["gateway"] is True
    assert captured["databricks_profile"] == "test-profile"
    assert captured["gateway_host"] == "https://example.databricks.com"
    assert captured["base_url_override"] == "https://example.databricks.com/ai-gateway/anthropic"
    assert captured["gateway_auth_command"] == "printf token"
    assert captured["gateway_auth_refresh_interval_ms"] == "900000"
    assert captured["cwd"] == "/tmp/test-cwd"
    assert captured["permission_mode"] == "acceptEdits"
    # When ``HARNESS_CLAUDE_SDK_OS_ENV`` is unset (this test
    # doesn't set it), the wrap defaults to ``caller_process +
    # sandbox=none`` so the SDK exposes its native tools to the
    # LLM. A regression that flipped this back to ``None`` would
    # silently disable Bash/Read/Edit/Write/Glob/Grep — the
    # whole point of step 5g's os_env threading. The check is
    # on the discriminating fields rather than identity so a
    # future tightening (different default sandbox, etc.) is a
    # one-line update.
    os_env_value = captured["os_env"]
    assert os_env_value is not None
    assert os_env_value.type == "caller_process"
    assert os_env_value.sandbox is not None
    assert os_env_value.sandbox.type == "none"


def test_executor_factory_cwd_falls_back_to_runner_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no ``HARNESS_CLAUDE_SDK_CWD``, the factory falls back to the
    runner's ``OMNIGENT_RUNNER_WORKSPACE`` (the folder the user launched
    in, and what the tmux terminal uses) rather than leaving cwd unset —
    which let the SDK root the CLI at the daemon's ``$HOME``. Mirrors the
    kimi / pi / hermes harnesses.
    """
    monkeypatch.delenv("HARNESS_CLAUDE_SDK_CWD", raising=False)
    monkeypatch.setenv("OMNIGENT_RUNNER_WORKSPACE", "/home/bobby/code/agents")

    captured: dict[str, Any] = {}

    def _fake_init(self: Any, *, cwd: str | None, **_kwargs: Any) -> None:
        captured["cwd"] = cwd

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    assert captured["cwd"] == "/home/bobby/code/agents"


def test_executor_factory_explicit_cwd_wins_over_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``HARNESS_CLAUDE_SDK_CWD`` takes precedence over the
    ``OMNIGENT_RUNNER_WORKSPACE`` fallback."""
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_CWD", "/tmp/explicit")
    monkeypatch.setenv("OMNIGENT_RUNNER_WORKSPACE", "/home/bobby/code/agents")

    captured: dict[str, Any] = {}

    def _fake_init(self: Any, *, cwd: str | None, **_kwargs: Any) -> None:
        captured["cwd"] = cwd

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    assert captured["cwd"] == "/tmp/explicit"


def test_executor_factory_decodes_os_env_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``HARNESS_CLAUDE_SDK_OS_ENV`` decodes into the inner OSEnvSpec.

    Omnigent serializes ``spec.executor.config["os_env"]`` via
    :func:`dataclasses.asdict` and JSON-encodes the result; the
    wrap must reconstruct an :class:`OSEnvSpec` (with nested
    sandbox spec) so :class:`ClaudeSDKExecutor` sees the same
    config a non-AP mode invocation would. Verifies the round-
    trip on a non-default payload — type, cwd, sandbox.type, and
    a sandbox boolean field all flow through.
    """
    import json

    monkeypatch.setenv(
        "HARNESS_CLAUDE_SDK_OS_ENV",
        json.dumps(
            {
                "type": "caller_process",
                "cwd": "/tmp/projected-cwd",
                "sandbox": {
                    "type": "linux_bwrap",
                    "read_paths": ["/srv/data"],
                    "write_paths": None,
                    "write_files": None,
                    # Non-default to prove every sandbox field
                    # round-trips, not just ``type``.
                    "allow_network": False,
                },
                "fork": False,
            }
        ),
    )

    captured: dict[str, Any] = {}

    def _fake_init(
        self: Any,
        *,
        cwd: str | None,
        os_env: Any,
        model: str | None,
        permission_mode: str,
        gateway: bool,
        databricks_profile: str | None,
        **_kwargs: Any,
    ) -> None:
        captured["os_env"] = os_env

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    os_env_value = captured["os_env"]
    assert os_env_value is not None
    # The ``cwd`` field carries the spec-author's choice. A
    # regression that dropped it would silently route the SDK
    # to the wrong working directory.
    assert os_env_value.cwd == "/tmp/projected-cwd"
    assert os_env_value.sandbox is not None
    assert os_env_value.sandbox.type == "linux_bwrap"
    # ``allow_network=False`` flowed through; a regression that
    # ignored sandbox-specific fields would leave it at the
    # default ``True``.
    assert os_env_value.sandbox.allow_network is False
    assert os_env_value.sandbox.read_paths == ["/srv/data"]


def test_executor_factory_falls_back_on_malformed_os_env_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed ``HARNESS_CLAUDE_SDK_OS_ENV`` falls back to default.

    A malformed payload should NOT crash the wrap — that would
    bring the whole subprocess down on first turn. The wrap
    instead logs a warning and defaults to the parity-preserving
    ``caller_process + sandbox=none`` so the agent still starts
    and the SDK natives stay enabled. Without this fallback a
    bad serialization on AP's side (or an env var hand-tweaked
    by an operator) could silently lobotomize the agent.
    """
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_OS_ENV", "{this-is-not-json")
    captured: dict[str, Any] = {}

    def _fake_init(
        self: Any,
        *,
        cwd: str | None,
        os_env: Any,
        model: str | None,
        permission_mode: str,
        gateway: bool,
        databricks_profile: str | None,
        **_kwargs: Any,
    ) -> None:
        captured["os_env"] = os_env

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    # Default kicks in: caller_process + sandbox=none. If the
    # wrap raised on bad JSON instead, the test (and the live
    # agent) would never see this assertion — the harness
    # subprocess would have crashed at first turn.
    os_env_value = captured["os_env"]
    assert os_env_value is not None
    assert os_env_value.type == "caller_process"
    assert os_env_value.sandbox is not None
    assert os_env_value.sandbox.type == "none"


@pytest.mark.parametrize(
    "raw_value,expected",
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("anything else", False),
    ],
)
def test_databricks_env_var_truthy_parsing(
    raw_value: str,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``HARNESS_CLAUDE_SDK_GATEWAY`` parses truthy strings only.

    The env-var-based contract requires explicit truthy strings
    to enable Databricks routing; anything else (including the
    empty string and unrecognized values) defaults to False.
    Catches a regression where the parser becomes loose and
    mistakenly enables Databricks based on stray env vars.
    """
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_GATEWAY", raw_value)
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    assert captured["gateway"] is expected


@pytest.mark.parametrize(
    "raw_value, expected",
    [
        ('"all"', "all"),
        ('"none"', "none"),
        ('["mlflow-onboarding"]', ["mlflow-onboarding"]),
        ('["a","b","c"]', ["a", "b", "c"]),
    ],
)
def test_skills_filter_env_var_decodes(
    raw_value: str,
    expected: str | list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``HARNESS_CLAUDE_SDK_SKILLS_FILTER`` decodes JSON into ``str``
    or ``list[str]``.

    The env-var bridge between the Omnigent runtime and the
    claude-sdk harness subprocess is the load-bearing surface
    for ``skills:`` plumbing — without it the harness wrap
    falls back to the constructor's ``"all"`` default and
    ignores the spec entirely. Verifies all three accepted
    shapes (``"all"``, ``"none"``, list) round-trip.
    """
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_SKILLS_FILTER", raw_value)
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    assert captured["skills_filter"] == expected


def test_skills_filter_env_var_missing_falls_back_to_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset ``HARNESS_CLAUDE_SDK_SKILLS_FILTER`` defaults to ``"all"``.

    Matches the SDK's ``skills="all"`` default so legacy
    deployments that don't yet ship the env var keep their
    existing host-skill discovery behavior.
    """
    monkeypatch.delenv("HARNESS_CLAUDE_SDK_SKILLS_FILTER", raising=False)
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    assert captured["skills_filter"] == "all"


def test_skills_filter_env_var_malformed_json_falls_back_to_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed JSON in ``HARNESS_CLAUDE_SDK_SKILLS_FILTER`` defaults
    to ``"all"`` rather than crashing the harness boot.

    A bad serialization shouldn't take the whole subprocess
    down on first turn — the wrap logs and degrades to the
    SDK's default skill-discovery behavior.
    """
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_SKILLS_FILTER", "{not-json")
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    assert captured["skills_filter"] == "all"


def test_bundle_dir_and_agent_name_env_vars_thread_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``HARNESS_CLAUDE_SDK_BUNDLE_DIR`` / ``_AGENT_NAME`` reach the
    inner executor.

    Together they wire the SDK ``--plugin-dir`` so any
    ``<bundle>/skills/<dir>/SKILL.md`` files in the agent's
    bundle get exposed as bundled skills, with a stable
    ``<agent>:<skill>`` plugin namespace via the
    ``.claude-plugin/plugin.json`` manifest the inner executor
    writes at construction time.
    """
    from pathlib import Path

    monkeypatch.setenv("HARNESS_CLAUDE_SDK_BUNDLE_DIR", "/tmp/fake/bundle")
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_AGENT_NAME", "hello_world")
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    assert captured["bundle_dir"] == Path("/tmp/fake/bundle")
    assert captured["agent_name"] == "hello_world"


def test_bundle_dir_unset_passes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing ``HARNESS_CLAUDE_SDK_BUNDLE_DIR`` resolves to ``None``.

    Catches a regression where the wrap silently coerces
    ``""`` to ``Path("")``: a bogus path that would break the
    SDK's ``--plugin-dir`` wiring rather than skipping it.
    """
    monkeypatch.delenv("HARNESS_CLAUDE_SDK_BUNDLE_DIR", raising=False)
    monkeypatch.delenv("HARNESS_CLAUDE_SDK_AGENT_NAME", raising=False)
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    assert captured["bundle_dir"] is None
    assert captured["agent_name"] is None


def test_iris_gets_a_strict_mcp_config_and_other_agents_do_not(monkeypatch):
    """Iris's contract is four read-only tools and no ambient ones.

    Without ``strict_mcp_config`` the CLI loads the operator's own MCP
    configuration on top of the servers we pass. A verified fixture turn
    carried 154 tools: Iris's four plus 150 from personal connectors,
    including ``Gmail__send_message`` and ``Calendar__delete_event``, on a
    session whose tool-boundary guardrail had failed to load.

    Scoped to Iris on purpose — an agent an operator built around their own
    connectors has a claim to them, and Iris does not. So this asserts both
    halves: on for her, off for everyone else.
    """
    from omnigent.inner.claude_sdk_harness import _is_iris_agent

    assert _is_iris_agent("iris") is True
    assert _is_iris_agent("iris (fork of iris)") is True
    assert _is_iris_agent("claude-native-ui") is False
    assert _is_iris_agent("cache cow") is False
    assert _is_iris_agent(None) is False
    assert _is_iris_agent("") is False


def test_eva_gets_a_strict_mcp_config_and_other_agents_do_not():
    """Eva's contract is the outreach tools and nothing ambient, as Iris's is.

    Measured on her bridge host before this: the CLI ``init`` of her runs on
    2026-09-23 and 24 listed 12 of the operator's claude.ai connector servers
    (Gmail, Slack, Drive, Ramp, Linear among them) and up to 422 of their
    tools, beside the one ``omnigent`` server Omnigent passed.
    """
    from omnigent.inner.claude_sdk_harness import _is_eva_agent, _is_iris_agent

    assert _is_eva_agent("eva") is True
    assert _is_eva_agent("eva (fork of eva)") is True
    assert _is_eva_agent("evaluator") is False
    assert _is_eva_agent("iris") is False
    assert _is_eva_agent("claude-native-ui") is False
    assert _is_eva_agent(None) is False
    assert _is_eva_agent("") is False
    # Neither helper claims the other's agent.
    assert _is_iris_agent("eva") is False


@pytest.mark.parametrize(
    ("agent_name", "strict"),
    [
        ("eva", True),
        ("eva (fork of eva)", True),
        ("iris", True),
        ("hello_world", False),
        ("cache cow", False),
    ],
)
def test_the_launch_carries_strict_mcp_config_for_eva_and_iris_only(
    monkeypatch: pytest.MonkeyPatch, agent_name: str, strict: bool
) -> None:
    """The executor the harness builds is what reaches the CLI flag.

    ``strict_mcp_config`` becomes ``ClaudeAgentOptions.strict_mcp_config``,
    which the SDK turns into ``--strict-mcp-config`` on the ``claude`` command
    line: the CLI then uses only the MCP servers passed to it.
    """
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_AGENT_NAME", agent_name)
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    assert captured["strict_mcp_config"] is strict


def test_iris_does_not_get_the_skill_tool_and_keeps_tool_search():
    """A policy can only refuse what it is asked about.

    Measured on production session 35340ac1, on a host carrying #30's ALLOW
    logging: ``ToolSearch`` ran and the turn made no ``/policies/evaluate``
    call for it. Invocation would have forced one — ``_can_use_tool_gate``
    reaches the evaluator for any non-``mcp__omnigent__`` tool — and an
    evaluator was wired, since the turn's other two evaluate calls happened.
    So the callback was not invoked for it, and a ``load_skill`` refusal
    carried there could not fire.

    ``CanUseToolShadowedWarning`` does not say otherwise: it is computed only
    from ``allowed_tools`` whole-tool entries, so it lists what IS shadowed
    and implies nothing about tools absent from it.

    Removing ``Skill`` is defence in depth regardless — it holds whether or
    not the gate is consulted. Safe for her specifically: she declares
    ``skills: none``, and ``bundle_root()`` folds every SKILL.md body into
    AGENTS.md, so her bundled skills reach her as instructions.

    Safe for her specifically: she declares ``skills: none``, and her three
    bundled skills reach her as instructions rather than as something to
    invoke — ``bundle_root()`` folds every SKILL.md body into AGENTS.md.

    ``ToolSearch`` stays: she uses it to locate her own tools, and it
    returns references rather than executing anything.
    """
    from omnigent.inner.claude_sdk_harness import _is_iris_agent

    def disallowed_for(agent_name):
        return ["Skill"] if _is_iris_agent(agent_name) else None

    assert disallowed_for("iris") == ["Skill"]
    assert "ToolSearch" not in (disallowed_for("iris") or [])
    assert disallowed_for("claude-native-ui") is None
    assert disallowed_for("cache cow") is None


@pytest.mark.parametrize(
    ("agent_name", "disallowed"),
    [
        ("iris", ["Skill"]),
        ("eva", ["Skill"]),
        ("eva (fork of eva)", ["Skill"]),
        ("claude-native-ui", None),
        ("cache cow", None),
        ("evangeline", None),
    ],
)
def test_the_launch_drops_the_skill_tool_for_eva_and_iris_only(
    monkeypatch: pytest.MonkeyPatch, agent_name: str, disallowed: list[str] | None
) -> None:
    """``disallowed_tools`` on the executor the harness builds, not a re-derivation.

    Eva gets it for Iris's reason: a measured Iris turn ran ``ToolSearch`` with
    no policy evaluation, so her ``tool_boundary`` denying ``Skill`` is not
    enough on its own. Safe for her: ``skills: none`` and no bundled skills.
    ``ToolSearch`` is never in the list.
    """
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_AGENT_NAME", agent_name)
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    assert captured["disallowed_tools"] == disallowed
    assert "ToolSearch" not in (captured["disallowed_tools"] or [])


# --------------------------------------------------------------------------
# Tally: the same protections as Eva, by name, and no one else's change
# --------------------------------------------------------------------------


def test_tally_is_recognised_by_name_and_nobody_else_is():
    from omnigent.inner.claude_sdk_harness import (
        _is_eva_agent,
        _is_iris_agent,
        _is_tally_agent,
    )

    assert _is_tally_agent("tally") is True
    assert _is_tally_agent("tally (fork of tally)") is True
    assert _is_tally_agent("tallyho") is False
    assert _is_tally_agent("eva") is False
    assert _is_tally_agent("iris") is False
    assert _is_tally_agent(None) is False
    assert _is_tally_agent("") is False
    # Neither of the other helpers claims her.
    assert _is_eva_agent("tally") is False
    assert _is_iris_agent("tally") is False


@pytest.mark.parametrize(
    ("agent_name", "strict", "disallowed"),
    [
        ("tally", True, ["Skill"]),
        ("tally (fork of tally)", True, ["Skill"]),
        ("eva", True, ["Skill"]),
        ("iris", True, ["Skill"]),
        ("tallyho", False, None),
        ("hello_world", False, None),
        ("cache cow", False, None),
    ],
)
def test_the_launch_carries_tallys_protections_and_changes_no_other_agent(
    monkeypatch: pytest.MonkeyPatch,
    agent_name: str,
    strict: bool,
    disallowed: list[str] | None,
) -> None:
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_AGENT_NAME", agent_name)
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    with patch(
        "omnigent.inner.claude_sdk_harness.ClaudeSDKExecutor.__init__",
        _fake_init,
    ):
        claude_sdk_harness._build_claude_sdk_executor()

    assert captured["strict_mcp_config"] is strict
    assert captured["disallowed_tools"] == disallowed
    assert "ToolSearch" not in (captured["disallowed_tools"] or [])


#: One of Tally's tools as the runner hands it to the harness, so the executor
#: builds its in-process ``omnigent`` MCP server the way a real turn does.
_TALLY_TOOL = {
    "name": "portal__get_health",
    "description": "Read the portal's health.",
    "parameters": {"type": "object", "properties": {}},
}


async def _tally_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    """The real ``ClaudeAgentOptions`` the harness builds for one Tally turn.

    Built by the harness's own factory and the executor's own ``run_turn``,
    with only the SDK client swapped for one that records its options and
    never starts a CLI.
    """
    import claude_agent_sdk

    from omnigent.inner import claude_sdk_executor

    monkeypatch.setenv("HARNESS_CLAUDE_SDK_AGENT_NAME", "tally")
    # What the runner sets for a `skills: none` spec.
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_SKILLS_FILTER", '"none"')
    monkeypatch.setenv("HARNESS_CLAUDE_SDK_CWD", str(tmp_path))
    monkeypatch.delenv("HARNESS_CLAUDE_SDK_GATEWAY", raising=False)
    captured: list[Any] = []

    class _RecordingClient:
        def __init__(self, options: Any) -> None:
            captured.append(options)

        async def connect(self) -> None:
            return None

        async def query(self, prompt: Any, session_id: str = "default") -> None:
            return None

        async def receive_response(self) -> Any:
            if False:
                yield None

        async def disconnect(self) -> None:
            return None

    class _SDK:
        def __getattr__(self, name: str) -> Any:
            return getattr(claude_agent_sdk, name)

        ClaudeSDKClient = _RecordingClient

    executor = claude_sdk_harness._build_claude_sdk_executor()
    with patch.object(claude_sdk_executor, "_ensure_sdk", return_value=_SDK()):
        async for _ in executor.run_turn(
            [{"role": "user", "content": "Is the portal healthy?"}], [_TALLY_TOOL], ""
        ):
            pass
    assert captured, "the executor never built a client"
    return captured[0]


async def test_tallys_real_options_carry_strict_mcp_no_settings_and_no_skill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The strongest check short of running the CLI: the real options object
    and the real command line the SDK would run for it.

    ``strict_mcp_config`` keeps the operator's claude.ai connectors out,
    ``setting_sources=[]`` keeps user and project settings (and their skills)
    out, and ``Skill`` is removed from the tool set outright.
    """
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    options = await _tally_options(monkeypatch, tmp_path)
    assert options.strict_mcp_config is True
    assert options.setting_sources == []
    assert options.disallowed_tools == ["Skill"]
    assert options.skills == []
    assert list(options.mcp_servers) == ["omnigent"]

    options.cli_path = "/nonexistent/claude"
    argv = SubprocessCLITransport(prompt="hi", options=options)._build_command()
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--disallowedTools") + 1] == "Skill"
    # Empty: the CLI loads no user or project settings, and no skills from them.
    assert "--setting-sources=" in argv


@pytest.mark.skipif(
    os.environ.get("OMNIGENT_REAL_CLAUDE_CLI_TESTS") != "1",
    reason=(
        "Launches the real `claude` CLI with the operator's own login. Opt in with "
        "OMNIGENT_REAL_CLAUDE_CLI_TESTS=1 on a machine where `claude` is installed "
        "and signed in; CI has neither."
    ),
)
async def test_the_real_cli_lists_only_the_omnigent_server_for_tally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Start the real CLI with Tally's options and read its ``init`` message.

    The CLI reports every MCP server it loaded in ``init``, before the model
    is asked anything. For Tally that list must be exactly ``omnigent``: no
    claude.ai connector (Gmail, Slack, Drive) from the operator's account.
    The session is closed as soon as ``init`` arrives.
    """
    import shutil

    import claude_agent_sdk

    cli = shutil.which("claude")
    if cli is None:
        pytest.skip("the `claude` CLI is not installed on this machine")
    options = await _tally_options(monkeypatch, tmp_path)
    options.cli_path = cli
    options.max_turns = 1
    # Running under Claude Code sets this; the executor unsets it the same way.
    monkeypatch.delenv("CLAUDECODE", raising=False)

    init: dict[str, Any] | None = None
    client = claude_agent_sdk.ClaudeSDKClient(options)
    await client.connect()
    try:
        await client.query("Reply with the single word OK.")
        async for message in client.receive_messages():
            if isinstance(message, claude_agent_sdk.SystemMessage) and message.subtype == "init":
                init = message.data
                break
    finally:
        await client.disconnect()

    assert init is not None, "the CLI never sent its init message"
    servers = [s.get("name") for s in init.get("mcp_servers", [])]
    assert servers == ["omnigent"], servers
    tools = init.get("tools", [])
    assert "Skill" not in tools
    assert not [t for t in tools if t.startswith("mcp__") and not t.startswith("mcp__omnigent__")]
