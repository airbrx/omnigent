"""add login_token_expires_at to hosts

Revision ID: abx3e2f3a4b5
Revises: abx2e2f3a4b5
Create Date: 2026-07-13 00:00:02.000000

Adds ``hosts.login_token_expires_at``: the Unix-epoch expiry of the login
token the host authenticates with, as the host reports it on its
``host.hello`` frame. Lets the admin hosts view show when each host's
credential lapses (and will need a re-login). Nullable — older hosts, and
auth modes that store no expiry, report none.

Renamed from ``q1a2b3c4d5e6`` and re-parented onto the upstream chain: upstream
independently used the ``q1a2b3c4d5e6`` id for an unrelated migration, so the
fork ids moved to the collision-proof ``abx*`` prefix. The DDL is guarded by
a column-existence check because deployments that ran the original
``q1a2b3c4d5e6`` already have the column while their ``alembic_version`` now
tracks the upstream chain.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "abx3e2f3a4b5"
down_revision: str | None = "abx2e2f3a4b5"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_column() -> bool:
    """Whether ``hosts.login_token_expires_at`` already exists (original fork migration ran)."""
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == "login_token_expires_at" for c in inspector.get_columns("hosts"))


def upgrade() -> None:
    """Add ``hosts.login_token_expires_at`` unless a prior fork deploy already added it."""
    if _has_column():
        return
    op.add_column("hosts", sa.Column("login_token_expires_at", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Drop ``hosts.login_token_expires_at`` if present."""
    if not _has_column():
        return
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.drop_column("login_token_expires_at")
