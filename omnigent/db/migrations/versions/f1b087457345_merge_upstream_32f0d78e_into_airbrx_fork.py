"""merge upstream 32f0d78e into airbrx fork

Revision ID: f1b087457345
Revises: 6287525878c2, za2b3c4d5e6f
Create Date: 2026-08-20 11:35:54.145694
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1b087457345'
down_revision: Union[str, None] = ('6287525878c2', 'za2b3c4d5e6f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
