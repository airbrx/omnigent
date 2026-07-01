"""Tests for the ``hosts.visibility`` column and its migration (r1a2b3c4d5e6).

Shared/always-on hosts: ``visibility`` is ``"shared"`` (any authenticated user
may reach the host) or private otherwise. The column is nullable with no
server default — ``NULL`` is treated as private by the reachability predicate,
so existing hosts stay owner-only after the migration applies to a populated DB.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from omnigent.db.utils import clear_engine_cache, get_or_create_engine


def _column(conn: sa.Connection, table: str, name: str) -> dict | None:
    for c in sa.inspect(conn).get_columns(table):
        if c["name"] == name:
            return c
    return None


def test_visibility_column_present_and_nullable_after_head(tmp_path: Path) -> None:
    """A full upgrade to head leaves ``hosts.visibility`` present and nullable.

    Absence means the migration didn't apply — the ORM (which declares the
    field) would then crash on every host read. Nullable matters: the
    reachability predicate treats NULL as private, so the column must be
    allowed to be NULL for pre-existing hosts.
    """
    engine = get_or_create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    try:
        with engine.connect() as conn:
            col = _column(conn, "hosts", "visibility")
        assert col is not None, "hosts.visibility missing — migration didn't apply"
        assert col["nullable"], "hosts.visibility must be nullable (NULL == private)"
    finally:
        clear_engine_cache()


def test_visibility_defaults_null_on_insert(tmp_path: Path) -> None:
    """Inserting a host without ``visibility`` lands as NULL (fail-safe private).

    A pre-existing host, or any connect that doesn't set visibility, must come
    up private — never accidentally shared. NULL is the fail-safe the
    reachability gate depends on.
    """
    engine = get_or_create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO hosts (owner, name, host_id, status, "
                    "created_at, updated_at) VALUES "
                    "('alice@x', 'laptop', 'host_z', 'online', 1, 1)"
                )
            )
        with engine.connect() as conn:
            got = conn.execute(
                sa.text("SELECT visibility FROM hosts WHERE host_id='host_z'")
            ).scalar_one()
        assert got is None, "a host inserted without visibility must be NULL (private)"
    finally:
        clear_engine_cache()
