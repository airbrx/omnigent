"""add os to hosts

Revision ID: abx2e2f3a4b5
Revises: abx1e2f3a4b5
Create Date: 2026-07-13 00:00:01.000000

Adds ``hosts.os``: the OS + arch string the host advertises on its
``host.hello`` frame (e.g. ``"Darwin 23.5.0 (arm64)"``). Persisted like
``version`` so the admin hosts view shows it for offline hosts too.
Nullable — older host builds (and rows predating this column) report
no OS.

Renamed from ``p1a2b3c4d5e6`` and re-parented onto the upstream chain: upstream
independently used the ``p1a2b3c4d5e6`` id for an unrelated migration, so the
fork ids moved to the collision-proof ``abx*`` prefix. The DDL is guarded by
a column-existence check because deployments that ran the original
``p1a2b3c4d5e6`` already have the column while their ``alembic_version`` now
tracks the upstream chain.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "abx2e2f3a4b5"
down_revision: str | None = "abx1e2f3a4b5"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_column() -> bool:
    """Whether ``hosts.os`` already exists (original fork migration ran)."""
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == "os" for c in inspector.get_columns("hosts"))


def upgrade() -> None:
    """Add ``hosts.os`` unless a prior fork deploy already added it."""
    if _has_column():
        return
    op.add_column("hosts", sa.Column("os", sa.String(length=128), nullable=True))


def downgrade() -> None:
    """Drop ``hosts.os`` if present."""
    if not _has_column():
        return
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.drop_column("os")
