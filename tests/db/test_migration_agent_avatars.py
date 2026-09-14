"""The agent_avatars table exists after a full migration run."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from tests.db.test_migration_connections import _upgrade


def test_agent_avatars_table_after_full_migration(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'avatars.db'}"
    engine = sa.create_engine(uri)
    _upgrade(uri, engine, "head")
    cols = {c["name"] for c in sa.inspect(engine).get_columns("agent_avatars")}
    assert cols == {
        "workspace_id",
        "agent_name",
        "artifact_key",
        "content_type",
        "updated_at",
    }
