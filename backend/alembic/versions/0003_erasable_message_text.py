"""Allow source message text to be erased after its last task is deleted.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("inbound_messages", "text", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE inbound_messages SET text = '' WHERE text IS NULL")
    op.alter_column("inbound_messages", "text", existing_type=sa.Text(), nullable=False)
