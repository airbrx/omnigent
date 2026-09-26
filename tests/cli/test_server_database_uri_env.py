"""``omnigent server`` reads its database URI from OMNIGENT_DATABASE_URI.

The flag on a command line is visible to every user on the host through
``ps``; the env binding keeps a password out of it. The flag still wins, so a
deployment that passes it keeps working.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from omnigent import cli as cli_module
from omnigent.cli import cli

ENV_URI = "postgresql+psycopg://env-user:env-pass@db.invalid:5432/omnigent"
FLAG_URI = "postgresql+psycopg://flag-user:flag-pass@db.invalid:5432/omnigent"


class _Resolved(Exception):
    """Stops the server once it has decided which database to use."""


@pytest.fixture
def resolved_uri(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def capture(db_uri: str) -> None:
        seen.append(db_uri)
        raise _Resolved

    monkeypatch.setattr(cli_module, "_ensure_sqlite_parent_dir", capture)
    monkeypatch.setattr(cli_module, "_apply_bind_auth_defaults", lambda host: None)
    monkeypatch.setattr(cli_module, "_load_global_config", dict)
    # The command writes this; monkeypatch restores whatever was there.
    monkeypatch.delenv("OMNIGENT_ACCOUNTS_AUTO_OPEN", raising=False)
    monkeypatch.delenv("OMNIGENT_DATABASE_URI", raising=False)
    return seen


def _run(args: list[str], env: dict[str, str] | None = None):
    return CliRunner().invoke(cli, ["server", "--host", "0.0.0.0", *args], env=env)


def test_the_env_var_is_read_when_the_flag_is_absent(resolved_uri: list[str]) -> None:
    result = _run([], env={"OMNIGENT_DATABASE_URI": ENV_URI})
    assert isinstance(result.exception, _Resolved), result.output
    assert resolved_uri == [ENV_URI]


def test_the_flag_beats_the_env_var(resolved_uri: list[str]) -> None:
    result = _run(["--database-uri", FLAG_URI], env={"OMNIGENT_DATABASE_URI": ENV_URI})
    assert isinstance(result.exception, _Resolved), result.output
    assert resolved_uri == [FLAG_URI]


def test_neither_falls_back_to_the_default_sqlite(resolved_uri: list[str]) -> None:
    """Unchanged, and dangerous: a missing setting silently becomes SQLite."""
    result = _run([])
    assert isinstance(result.exception, _Resolved), result.output
    assert resolved_uri[0].startswith("sqlite")
