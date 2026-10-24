"""Create the initial persistence schema

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("dashboard_token_hash", sa.Text(), nullable=True),
        sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("dashboard_token_hash", name=op.f("uq_users_dashboard_token_hash")),
        sa.UniqueConstraint("telegram_user_id", name=op.f("uq_users_telegram_user_id")),
    )
    op.create_table(
        "inbound_messages",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "text", "voice", name="kind", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "extractor",
            sa.Enum(
                "heuristic",
                "claude",
                name="extractor",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_inbound_messages_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inbound_messages")),
    )
    op.create_index(
        op.f("ix_inbound_messages_user_id"), "inbound_messages", ["user_id"], unique=False
    )
    op.create_index(
        "uq_inbound_messages_telegram_ref",
        "inbound_messages",
        ["telegram_chat_id", "telegram_message_id"],
        unique=True,
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("inbound_message_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "in_progress",
                "completed",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(description) <= 2000", name=op.f("ck_tasks_description_length")
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 120", name=op.f("ck_tasks_title_length")
        ),
        sa.CheckConstraint("position BETWEEN 0 AND 9", name=op.f("ck_tasks_position")),
        sa.ForeignKeyConstraint(
            ["inbound_message_id"],
            ["inbound_messages.id"],
            name=op.f("fk_tasks_inbound_message_id_inbound_messages"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_tasks_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_index(
        "ix_tasks_user_message_position",
        "tasks",
        ["user_id", sa.literal_column("inbound_message_id DESC"), "position"],
        unique=False,
    )
    op.create_index(
        "uq_tasks_message_position", "tasks", ["inbound_message_id", "position"], unique=True
    )
    op.create_table(
        "voice_jobs",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("inbound_message_id", sa.Integer(), nullable=True),
        sa.Column("ack_message_id", sa.BigInteger(), nullable=True),
        sa.Column("file_id", sa.Text(), nullable=False),
        sa.Column("file_unique_id", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "processing",
                "done",
                "failed",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "failure_kind",
            sa.Enum(
                "too_large",
                "unsupported_audio",
                "empty_transcript",
                "transcription_timeout",
                "provider_unavailable",
                "transient_exhausted",
                "internal",
                "stuck",
                name="failure_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column(
            "provider",
            sa.Enum(
                "fake",
                "openai",
                "whisper_server",
                name="provider",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("notify_attempts", sa.Integer(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "notification_outcome",
            sa.Enum(
                "edited",
                "replied",
                "blocked",
                "given_up",
                name="notification_outcome",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("notify_not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "notified_at IS NULL OR status IN ('done', 'failed')",
            name=op.f("ck_voice_jobs_notified_terminal"),
        ),
        sa.CheckConstraint(
            "notify_not_before IS NULL OR status IN ('done', 'failed')",
            name=op.f("ck_voice_jobs_deferred_terminal"),
        ),
        sa.CheckConstraint(
            "status <> 'done' OR inbound_message_id IS NOT NULL",
            name=op.f("ck_voice_jobs_done_has_message"),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR failure_kind IS NOT NULL",
            name=op.f("ck_voice_jobs_failed_has_kind"),
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 5", name=op.f("ck_voice_jobs_attempts")),
        sa.CheckConstraint(
            "notify_attempts BETWEEN 0 AND 5", name=op.f("ck_voice_jobs_notify_attempts")
        ),
        sa.ForeignKeyConstraint(
            ["inbound_message_id"],
            ["inbound_messages.id"],
            name=op.f("fk_voice_jobs_inbound_message_id_inbound_messages"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_voice_jobs_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voice_jobs")),
    )
    op.create_index(
        "ix_voice_jobs_pending_last_enqueued",
        "voice_jobs",
        ["last_enqueued_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'processing') OR notified_at IS NULL"),
    )
    op.create_index(op.f("ix_voice_jobs_user_id"), "voice_jobs", ["user_id"], unique=False)
    op.create_index(
        "uq_voice_jobs_telegram_ref",
        "voice_jobs",
        ["telegram_chat_id", "telegram_message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_voice_jobs_telegram_ref", table_name="voice_jobs")
    op.drop_index(op.f("ix_voice_jobs_user_id"), table_name="voice_jobs")
    op.drop_index(
        "ix_voice_jobs_pending_last_enqueued",
        table_name="voice_jobs",
        postgresql_where=sa.text("status IN ('queued', 'processing') OR notified_at IS NULL"),
    )
    op.drop_table("voice_jobs")
    op.drop_index("uq_tasks_message_position", table_name="tasks")
    op.drop_index("ix_tasks_user_message_position", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("uq_inbound_messages_telegram_ref", table_name="inbound_messages")
    op.drop_index(op.f("ix_inbound_messages_user_id"), table_name="inbound_messages")
    op.drop_table("inbound_messages")
    op.drop_table("users")
