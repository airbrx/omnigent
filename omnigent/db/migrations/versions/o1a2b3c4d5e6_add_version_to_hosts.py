"""add version column to hosts

Revision ID: o1a2b3c4d5e6
Revises: n1a2b3c4d5e6
Create Date: 2026-06-27 21:30:00.000000

Adds ``hosts.version``: the version string the host advertises on its
``host.hello`` frame (e.g. ``"0.1.0"``). Persisting it means an admin can
see the last-known version of an **offline** host (the live value is only
in the in-memory tunnel registry for connected hosts), so the admin hosts
view can answer "which hosts are out of date?". Nullable — older host
builds, and rows that predate this column, report no version.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "o1a2b3c4d5e6"
down_revision: str | None = "n1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable ``version`` column to ``hosts``.

    Nullable with no ``server_default`` — existing rows keep ``NULL``
    ("unknown") until the host next reconnects and reports its version.
    Batch mode for SQLite compatibility, consistent with the other host
    migrations.
    """
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.add_column(sa.Column("version", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Drop the ``version`` column."""
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.drop_column("version")
