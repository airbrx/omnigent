"""add login_token_expires_at column to hosts

Revision ID: q1a2b3c4d5e6
Revises: p1a2b3c4d5e6
Create Date: 2026-06-28 21:00:00.000000

Adds ``hosts.login_token_expires_at``: the Unix-epoch expiry of the login token
the host authenticates with, as the host reports it on its ``host.hello``
frame. Lets the admin hosts view show when each host's credential lapses
(and will need a re-login). Nullable — older hosts, and auth modes that
store no expiry, report none.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q1a2b3c4d5e6"
down_revision: str | None = "p1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable ``login_token_expires_at`` column to ``hosts``.

    Nullable with no ``server_default``; batch mode for SQLite
    compatibility, consistent with the other host migrations.
    """
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.add_column(sa.Column("login_token_expires_at", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Drop the ``login_token_expires_at`` column."""
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.drop_column("login_token_expires_at")
