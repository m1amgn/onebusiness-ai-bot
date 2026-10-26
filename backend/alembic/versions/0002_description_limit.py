"""Allow task descriptions up to the Telegram message length.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_tasks_description_length"), "tasks", type_="check")
    op.create_check_constraint(
        op.f("ck_tasks_description_length"), "tasks", "char_length(description) <= 4096"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_tasks_description_length"), "tasks", type_="check")
    op.create_check_constraint(
        op.f("ck_tasks_description_length"), "tasks", "char_length(description) <= 2000"
    )
