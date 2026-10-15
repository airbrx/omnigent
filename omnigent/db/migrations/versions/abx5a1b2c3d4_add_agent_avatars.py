"""add agent_avatars table

Revision ID: abx5a1b2c3d4
Revises: 0c8ef9677127
Create Date: 2026-09-14 00:00:00.000000

airbrx-local table backing agent avatars in the drawer. Image bytes live
in the artifact store; this table holds only the mapping and the metadata
the image route needs for cache validation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "abx5a1b2c3d4"
down_revision: str | None = "0c8ef9677127"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table() -> bool:
    """Whether ``agent_avatars`` already exists."""
    return sa.inspect(op.get_bind()).has_table("agent_avatars")


def upgrade() -> None:
    """Create ``agent_avatars`` unless the schema already carries it.

    CockroachDB bootstraps from current ORM metadata and stamps head, then
    treats every revision after ``CRDB_BASELINE_REVISION`` as replayable
    against an already-complete schema (see ``omnigent/db/cockroachdb.py``,
    and upstream's own ``gg1b2c3d4e5f`` which inspects before acting). This
    table is in that metadata, so an unguarded ``create_table`` raises
    ``DuplicateTable`` on that path — the fork's other migrations already
    guard for the same reason.
    """
    if _has_table():
        return
    op.create_table(
        "agent_avatars",
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(length=255), nullable=False),
        sa.Column("artifact_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "agent_name"),
    )


def downgrade() -> None:
    """Drop ``agent_avatars`` if it is there."""
    if not _has_table():
        return
    op.drop_table("agent_avatars")
