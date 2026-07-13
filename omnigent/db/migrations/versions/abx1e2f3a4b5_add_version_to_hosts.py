"""add version to hosts

Revision ID: abx1e2f3a4b5
Revises: z5a2b3c4d5e6
Create Date: 2026-07-13 00:00:00.000000

Adds ``hosts.version``: the version string the host advertises on its
``host.hello`` frame (e.g. ``"0.1.0"``). Persisting it means an admin can
see the last-known version of an **offline** host (the live value is only
in the in-memory tunnel registry for connected hosts), so the admin hosts
view can answer "which hosts are out of date?". Nullable — older host
builds, and rows that predate this column, report no version.

Renamed from ``o1a2b3c4d5e6`` and re-parented onto the upstream chain: upstream
independently used the ``o1a2b3c4d5e6`` id for an unrelated migration, so the
fork ids moved to the collision-proof ``abx*`` prefix. The DDL is guarded by
a column-existence check because deployments that ran the original
``o1a2b3c4d5e6`` already have the column while their ``alembic_version`` now
tracks the upstream chain.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "abx1e2f3a4b5"
down_revision: str | None = "z5a2b3c4d5e6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_column() -> bool:
    """Whether ``hosts.version`` already exists (original fork migration ran)."""
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == "version" for c in inspector.get_columns("hosts"))


def upgrade() -> None:
    """Add ``hosts.version`` unless a prior fork deploy already added it."""
    if _has_column():
        return
    op.add_column("hosts", sa.Column("version", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Drop ``hosts.version`` if present."""
    if not _has_column():
        return
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.drop_column("version")
