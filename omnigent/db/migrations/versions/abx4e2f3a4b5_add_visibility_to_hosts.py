"""add visibility + workroot columns to hosts

Revision ID: abx4e2f3a4b5
Revises: abx3e2f3a4b5
Create Date: 2026-07-13 00:00:03.000000

Adds two columns for shared, always-on hosts (host-declared via
``omnigent host --shared``):

- ``hosts.visibility``: reachability, ``"private"`` (owner-only, default) or
  ``"shared"`` (any authenticated user, confined + shell-less). Nullable, no
  server default; ``NULL`` == private (a row must be explicitly ``"shared"``
  to relax the ownership gate), so existing hosts stay owner-only.
- ``hosts.workroot``: the directory a non-owner session is jailed to when the
  host is shared. NULL when private.

Also adds ``ck_hosts_visibility`` so only ``NULL``/``'private'``/``'shared'``
can ever persist.

Renamed from ``r1a2b3c4d5e6`` (upstream reused that id for the workspace_id
migration) and guarded by a column-existence check for deployments that
already ran the original.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "abx4e2f3a4b5"
down_revision: str | None = "abx3e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column() -> bool:
    """Whether ``hosts.visibility`` already exists (original migration ran)."""
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == "visibility" for c in inspector.get_columns("hosts"))


def upgrade() -> None:
    """Add the nullable ``visibility`` + ``workroot`` columns to ``hosts``
    and a check constraint on ``visibility``.

    Nullable with no ``server_default`` (NULL == private, the fail-safe);
    batch mode for SQLite compatibility, consistent with the other host
    migrations. The batch recreate applies ``ck_hosts_visibility``.
    """
    if _has_column():
        return
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.add_column(sa.Column("visibility", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("workroot", sa.String(length=4096), nullable=True))
        batch_op.create_check_constraint(
            "ck_hosts_visibility",
            "visibility IS NULL OR visibility IN ('private', 'shared')",
        )


def downgrade() -> None:
    """Drop the check constraint and both columns."""
    if not _has_column():
        return
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.drop_constraint("ck_hosts_visibility", type_="check")
        batch_op.drop_column("workroot")
        batch_op.drop_column("visibility")
