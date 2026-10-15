"""Registering Iris must not be fatal.

Eva's registration was made non-fatal after a bad bundle crash-looped
omnigent.airbrx.ai twice in one night (``test_eva_registration.py``). Iris is
registered by the same ``serve`` block a few lines above and was left
unguarded, so a malformed bundle or a failed archive integrity check would
still take the whole coordinator down, along with every ordinary session that
has nothing to do with her.

These tests drive the real ``omnigent server`` command end to end with the
blocking uvicorn loop stubbed out, the way ``tests/cli/test_cli.py`` does, so
they exercise the actual registration block rather than a copy of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from omnigent.cli import cli

PACKAGE = "omnigent.airbrx.iris.package"


def _bind_iris(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point ``OMNIGENT_IRIS_CONFIG`` at one valid fixture binding.

    Bindings gate the whole block: without one the server never tries to
    register Iris and these tests would pass for the wrong reason.
    """
    config = tmp_path / "binding.json"
    config.write_text(
        json.dumps(
            [
                {
                    "tenant_id": "fixture-ci",
                    "host_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "workspace": str(tmp_path.resolve()),
                    "users": ["fixture-owner"],
                    "fixture": True,
                    "pat_ref": "",
                }
            ]
        )
    )
    monkeypatch.setenv("OMNIGENT_IRIS_CONFIG", str(config))


def _run_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Result, dict[str, Any]]:
    """Run ``omnigent server`` up to the point uvicorn would block.

    Mirrors ``test_server_command_reads_tunnel_token_and_does_not_spawn_runner``:
    the uvicorn loop is replaced so nothing binds a port, and the local-server
    bookkeeping is pinned so the test never reuses or records a real server.
    """
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


def _registered_iris() -> Any:
    """The Iris row the server just wrote, or ``None``."""
    from omnigent.runtime import get_agent_store

    return get_agent_store().get_by_name("iris")


def test_a_malformed_bundle_keeps_the_server_up_and_names_the_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bundle ``load`` rejects costs Iris her registration and nothing else."""
    _bind_iris(monkeypatch, tmp_path)
    bad = tmp_path / "bad-bundle"
    bad.mkdir()
    (bad / "config.yaml").write_text("name: iris\n")
    monkeypatch.setattr(f"{PACKAGE}.bundle_root", lambda: bad)

    result, captured = _run_server(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    assert captured.get("uvicorn_called") is True
    assert "Iris is bound but did not register" in result.output
    assert "absent from the agent catalog and drawer" in result.output
    assert "missing required field: spec_version" in result.output
    assert _registered_iris() is None


def test_a_failed_integrity_check_keeps_the_server_up_and_names_the_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A digest mismatch is raised by ``source_root`` before any bundle exists.

    Both the bundle and the portrait come out of that archive, so the check
    fails for the registration and for the avatar alike. The server must
    survive both.
    """
    _bind_iris(monkeypatch, tmp_path)

    def _tampered() -> Path:
        raise RuntimeError("Iris package integrity check failed")

    monkeypatch.setattr(f"{PACKAGE}.source_root", _tampered)
    monkeypatch.setattr(f"{PACKAGE}.bundle_root", _tampered)

    result, captured = _run_server(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    assert captured.get("uvicorn_called") is True
    assert "Iris is bound but did not register" in result.output
    assert "Iris package integrity check failed" in result.output
    assert _registered_iris() is None


def test_a_good_bundle_still_registers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The guard changes nothing on the path that has always worked."""
    _bind_iris(monkeypatch, tmp_path)

    result, captured = _run_server(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    assert captured.get("uvicorn_called") is True
    assert "did not register" not in result.output
    assert _registered_iris() is not None
