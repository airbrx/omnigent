"""Registering Tally: gated on bindings, needs no secret, and is never fatal.

The first three tests are Eva's registration properties with Tally's names.
The rest drive the real ``omnigent server`` command with the uvicorn loop
stubbed, the way ``test_iris_registration.py`` does, so they exercise the
actual block in ``omnigent/cli.py`` rather than a copy of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from omnigent.airbrx.tally.package import bundle_root
from omnigent.airbrx.tally.runtime import PORTAL_TOKEN_VAR, PORTAL_URL_VAR
from omnigent.cli import cli
from omnigent.runtime import launch_env as launch_env_registry
from omnigent.spec import load

USER = "aerickson@airbrx.com"
REF = "env:AIRBRX_TALLY_MCP_TOKEN"


@pytest.fixture(autouse=True)
def _clean_registry():
    launch_env_registry.reset()
    yield
    launch_env_registry.reset()


def test_the_bundle_validates_without_any_of_her_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PORTAL_TOKEN_VAR, raising=False)
    monkeypatch.delenv(PORTAL_URL_VAR, raising=False)
    assert load(bundle_root(), expand_env=False).name == "tally"


def test_expanding_her_bundle_on_the_coordinator_still_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PORTAL_TOKEN_VAR, raising=False)
    monkeypatch.delenv(PORTAL_URL_VAR, raising=False)
    with pytest.raises(Exception) as caught:
        load(bundle_root(), expand_env=True)
    assert "TALLY_PORTAL_MCP" in str(caught.value)


def test_the_stored_artifact_is_the_unexpanded_bundle() -> None:
    config = (bundle_root() / "config.yaml").read_text()
    assert "${TALLY_PORTAL_MCP_TOKEN}" in config
    assert "${TALLY_PORTAL_MCP_URL}" in config


# --------------------------------------------------------------------------
# The server's registration block
# --------------------------------------------------------------------------


def _bind_tally(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """One live, hostless binding: the shape Abram chose."""
    config = tmp_path / "tally.json"
    config.write_text(
        json.dumps([{"users": [USER], "base_url": "http://127.0.0.1:4318", "token_ref": REF}])
    )
    monkeypatch.setenv("OMNIGENT_TALLY_CONFIG", str(config))


def _run_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Result, dict[str, Any]]:
    """Run ``omnigent server`` up to the point uvicorn would block."""
    import uvicorn.server

    from omnigent.host import local_server as _local_server_mod

    captured: dict[str, Any] = {}

    def _fake_server_run(self: Any) -> None:
        captured["uvicorn_called"] = True

    monkeypatch.setattr(uvicorn.server.Server, "run", _fake_server_run)
    monkeypatch.setattr(_local_server_mod, "local_server_url_if_healthy", lambda: None)
    monkeypatch.setattr(_local_server_mod, "pick_local_port", lambda preferred: preferred)
    monkeypatch.setattr(_local_server_mod, "register_local_server", lambda port: None)
    monkeypatch.setattr(_local_server_mod, "clear_local_server_record", lambda: None)

    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "config.yaml").write_text("")
    monkeypatch.setenv("OMNIGENT_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("OMNIGENT_EVA_CONFIG", raising=False)
    monkeypatch.delenv("OMNIGENT_IRIS_CONFIG", raising=False)
    # The coordinator never holds her token; registration must not need it.
    monkeypatch.delenv(PORTAL_TOKEN_VAR, raising=False)
    monkeypatch.delenv("AIRBRX_TALLY_MCP_TOKEN", raising=False)

    result = CliRunner().invoke(
        cli,
        [
            "server",
            "--host",
            "127.0.0.1",
            "--port",
            "9999",
            "--no-open",
            "--database-uri",
            f"sqlite:///{tmp_path / 'chat.db'}",
            "--artifact-location",
            str(tmp_path / "artifacts"),
        ],
    )
    return result, captured


def _registered_tally() -> Any:
    from omnigent.runtime import get_agent_store

    return get_agent_store().get_by_name("tally")


def test_no_binding_registers_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OMNIGENT_TALLY_CONFIG", raising=False)
    result, captured = _run_server(monkeypatch, tmp_path)
    assert result.exit_code == 0, result.output
    assert captured.get("uvicorn_called") is True
    assert _registered_tally() is None
    assert launch_env_registry.collect("tally", USER).is_empty()


def test_a_binding_registers_her_and_her_launch_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _bind_tally(monkeypatch, tmp_path)
    result, captured = _run_server(monkeypatch, tmp_path)
    assert result.exit_code == 0, result.output
    assert captured.get("uvicorn_called") is True
    assert "did not register" not in result.output
    assert _registered_tally() is not None
    launch = launch_env_registry.collect("tally", USER)
    assert launch.env == {PORTAL_URL_VAR: "http://127.0.0.1:4318/mcp"}
    assert launch.secret_refs == {PORTAL_TOKEN_VAR: REF}
    # Iris and Eva are not affected by Tally's provider.
    assert launch_env_registry.collect("eva", USER).is_empty()
    assert launch_env_registry.collect("iris", USER).is_empty()


def test_a_malformed_bundle_keeps_the_server_up_and_names_the_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _bind_tally(monkeypatch, tmp_path)
    bad = tmp_path / "bad-bundle"
    bad.mkdir()
    (bad / "config.yaml").write_text("name: tally\n")
    monkeypatch.setattr("omnigent.airbrx.tally.package.bundle_root", lambda: bad)

    result, captured = _run_server(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    assert captured.get("uvicorn_called") is True
    assert "Tally is bound but did not register" in result.output
    assert _registered_tally() is None
