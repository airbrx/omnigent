"""merge upstream v0.14.0 into airbrx fork

Revision ID: 7072b69b4c10
Revises: abx5a1b2c3d4, gg1b2c3d4e5f
Create Date: 2026-09-15 07:40:15.869416

No-op. Reconciles this fork's branch (the five extra ``hosts`` columns plus
``agent_avatars``) with upstream's v0.14.0 head.

v0.14's ``gg1b2c3d4e5f`` alters ``hosts.deleted_at`` to BigInteger and adds
the reaper traversal indexes. That is upstream's own column, disjoint from
ours (version / os / login_token_expires_at / visibility / workroot), so the
two branches still need only a head reconciliation and no data migration.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "7072b69b4c10"
down_revision: str | Sequence[str] | None = ("abx5a1b2c3d4", "gg1b2c3d4e5f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
