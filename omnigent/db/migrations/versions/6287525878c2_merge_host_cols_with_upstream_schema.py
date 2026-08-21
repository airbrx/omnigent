"""merge host cols with upstream schema

Revision ID: 6287525878c2
Revises: b3c4d5e6f7a8, abx4e2f3a4b5
Create Date: 2026-07-25 09:33:10.888155
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "6287525878c2"
down_revision: str | Sequence[str] | None = ("b3c4d5e6f7a8", "abx4e2f3a4b5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
