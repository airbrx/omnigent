"""merge upstream v0.12.0 into airbrx fork

Revision ID: 09a6063c8614
Revises: f1b087457345, ga1b2c3d4e5f
Create Date: 2026-09-13 17:25:51.537399

No-op: reconciles the airbrx host-column branch (version/os/
login_token_expires_at/visibility on ``hosts``) with upstream's v0.12.0
head. The two branches touch disjoint schema, so there is nothing to
apply — this revision exists only to give Alembic a single head.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "09a6063c8614"
down_revision: str | Sequence[str] | None = ("f1b087457345", "ga1b2c3d4e5f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
