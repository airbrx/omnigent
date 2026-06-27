"""add os column to hosts

Revision ID: p1a2b3c4d5e6
Revises: o1a2b3c4d5e6
Create Date: 2026-06-27 23:40:00.000000

Adds ``hosts.os``: the OS + arch string the host advertises on its
``host.hello`` frame (e.g. ``"Darwin 23.5.0 (arm64)"``). Persisted like
``version`` so the admin hosts view shows it for offline hosts too.
Nullable — older host builds (and rows predating this column) report
no OS.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p1a2b3c4d5e6"
down_revision: str | None = "o1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable ``os`` column to ``hosts``.

    Nullable with no ``server_default``; batch mode for SQLite
    compatibility, consistent with the other host migrations.
    """
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.add_column(sa.Column("os", sa.String(length=128), nullable=True))


def downgrade() -> None:
    """Drop the ``os`` column."""
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.drop_column("os")
