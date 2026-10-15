"""``env_expansion: runner``: the server never expands the bundle; the runner still does.

The defect this exists for: an operator agent whose MCP header names a per-host
secret (``Authorization: Bearer ${TOKEN}``) could not have a session created,
because every server-side load expanded the bundle against the server's own
environment and refused the unset variable. The only way round it was to put
the secret, or a stand-in, in the server's environment.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from omnigent.errors import OmnigentError
from omnigent.spec import load
from omnigent.spec.parser import declared_env_expansion

VAR = "OMNIGENT_TEST_RUNNER_ONLY_TOKEN"


def _bundle(root: Path, *, env_expansion: str | None) -> Path:
    config: dict[str, object] = {
        "spec_version": 1,
        "name": "runner-only",
        "executor": {"type": "omnigent", "config": {"harness": "claude-sdk"}},
        "tools": {
            "remote": {
                "type": "mcp",
                "url": "http://127.0.0.1:9/mcp/",
                "headers": {"Authorization": "Bearer ${" + VAR + "}"},
            }
        },
    }
    if env_expansion is not None:
        config["env_expansion"] = env_expansion
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    return root


def _auth(spec) -> str:
    (server,) = [s for s in spec.mcp_servers if s.name == "remote"]
    return server.headers["Authorization"]


def test_default_is_everywhere(tmp_path: Path) -> None:
    assert declared_env_expansion(_bundle(tmp_path, env_expansion=None)) == "everywhere"


def test_an_unknown_value_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VAR, "x")
    with pytest.raises(OmnigentError, match="env_expansion"):
        load(_bundle(tmp_path, env_expansion="server"), expand_env=True)


def test_the_parsed_spec_records_the_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(VAR, raising=False)
    spec = load(_bundle(tmp_path, env_expansion="runner"), expand_env=False)
    assert spec.env_expansion == "runner"


def test_server_side_load_of_a_runner_only_bundle_does_not_need_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fix. The server asks for expansion and gets none, and no error."""
    monkeypatch.delenv(VAR, raising=False)
    spec = load(_bundle(tmp_path, env_expansion="runner"), expand_env=True, server_side=True)
    assert _auth(spec) == "Bearer ${" + VAR + "}"


def test_server_side_load_never_reads_a_runner_only_secret_even_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If an operator did put the value in the server env, it still is not used."""
    monkeypatch.setenv(VAR, "server-side-value")
    spec = load(_bundle(tmp_path, env_expansion="runner"), expand_env=True, server_side=True)
    assert "server-side-value" not in _auth(spec)


def test_the_runner_still_expands_a_runner_only_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half. The runner does not pass server_side, so it resolves as before."""
    monkeypatch.setenv(VAR, "runner-value")
    spec = load(_bundle(tmp_path, env_expansion="runner"), expand_env=True)
    assert _auth(spec) == "Bearer runner-value"


def test_an_ordinary_operator_bundle_still_expands_server_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing changes for a bundle that does not opt out."""
    monkeypatch.setenv(VAR, "value")
    spec = load(_bundle(tmp_path, env_expansion=None), expand_env=True, server_side=True)
    assert _auth(spec) == "Bearer value"


def test_an_ordinary_operator_bundle_still_refuses_an_unset_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(VAR, raising=False)
    with pytest.raises(OmnigentError, match="Unresolved environment variable"):
        load(_bundle(tmp_path, env_expansion=None), expand_env=True, server_side=True)


def test_server_side_never_turns_expansion_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session-scoped bundle is loaded with expand_env=False and must stay unexpanded."""
    monkeypatch.setenv(VAR, "value")
    for declared in (None, "everywhere", "runner"):
        root = _bundle(tmp_path / str(declared), env_expansion=declared)
        spec = load(root, expand_env=False, server_side=True)
        assert _auth(spec) == "Bearer ${" + VAR + "}"
