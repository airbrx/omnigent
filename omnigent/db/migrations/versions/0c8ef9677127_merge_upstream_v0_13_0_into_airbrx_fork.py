"""merge upstream v0.13.0 into airbrx fork

Revision ID: 0c8ef9677127
Revises: 09a6063c8614, ge1b2c3d4e5f
Create Date: 2026-09-13 18:30:48.011182

No-op. Stage 2 of the v0.11 -> v0.13 sync. v0.13 is the first upstream
release to add a column to ``hosts`` (``deleted_at``, the managed-sandbox
tombstone), the same table this fork extends with version / os /
login_token_expires_at / visibility / workroot — but the columns are
disjoint, so the two branches still need only a head reconciliation and
no data migration.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0c8ef9677127"
down_revision: str | Sequence[str] | None = ("09a6063c8614", "ge1b2c3d4e5f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
