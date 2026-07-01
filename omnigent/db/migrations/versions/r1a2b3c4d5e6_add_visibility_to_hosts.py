"""add visibility column to hosts

Revision ID: r1a2b3c4d5e6
Revises: q1a2b3c4d5e6
Create Date: 2026-07-01 10:30:00.000000

Adds ``hosts.visibility``: host reachability, ``"private"`` (owner-only, the
default) or ``"shared"`` (any authenticated user may dispatch to / view /
browse it). Backs shared, always-on hosts — a machine a user can target when
their own laptop is offline. Nullable with no server default; ``NULL`` is
treated as ``"private"`` by the reachability predicate (a row must be
explicitly ``"shared"`` to relax the ownership gate), so existing hosts stay
owner-only after the migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r1a2b3c4d5e6"
down_revision: str | None = "q1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable ``visibility`` column to ``hosts``.

    Nullable with no ``server_default`` (NULL == private, the fail-safe);
    batch mode for SQLite compatibility, consistent with the other host
    migrations.
    """
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.add_column(sa.Column("visibility", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Drop the ``visibility`` column."""
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.drop_column("visibility")
