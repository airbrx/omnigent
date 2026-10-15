"""merge upstream f4f81681c into airbrx fork

Revision ID: 0d24f49ad180
Revises: f1b087457345, mm1a2b3c4d5e
Create Date: 2026-10-15 11:09:42.009566
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0d24f49ad180"
down_revision: str | Sequence[str] | None = ("7072b69b4c10", "mm1a2b3c4d5e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
