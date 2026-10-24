from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from taskbot.domain.extraction import ExtractorName
from taskbot.domain.messages import MessageKind
from taskbot.domain.tasks import TaskStatus
from taskbot.domain.voice_jobs import (
    FailureKind,
    NotificationOutcome,
    TranscriptionProvider,
    VoiceJobStatus,
)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def concrete_table(model: type[Base]) -> Table:
    # DeclarativeBase types __table__ as the broader FromClause, which can include views.
    table = model.__table__
    if not isinstance(table, Table):
        raise TypeError("Persistence repositories require a concrete table")
    return table


def string_enum(enum: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum,
        native_enum=False,
        create_constraint=True,
        length=32,
        name=name,
        values_callable=lambda members: [item.value for item in members],
    )


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    dashboard_token_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    token_version: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InboundMessageRow(Base):
    __tablename__ = "inbound_messages"
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[MessageKind] = mapped_column(string_enum(MessageKind, "kind"))
    text: Mapped[str] = mapped_column(Text)
    extractor: Mapped[ExtractorName] = mapped_column(string_enum(ExtractorName, "extractor"))
    degraded: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index(
            "uq_inbound_messages_telegram_ref",
            "telegram_chat_id",
            "telegram_message_id",
            unique=True,
        ),
    )


class TaskRow(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    inbound_message_id: Mapped[int] = mapped_column(ForeignKey("inbound_messages.id"))
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(string_enum(TaskStatus, "status"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("uq_tasks_message_position", "inbound_message_id", "position", unique=True),
        Index(
            "ix_tasks_user_message_position", "user_id", text("inbound_message_id DESC"), "position"
        ),
        CheckConstraint("char_length(title) BETWEEN 1 AND 120", name="title_length"),
        CheckConstraint("char_length(description) <= 2000", name="description_length"),
        CheckConstraint("position BETWEEN 0 AND 9", name="position"),
    )


class VoiceJobRow(Base):
    __tablename__ = "voice_jobs"
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    inbound_message_id: Mapped[int | None] = mapped_column(ForeignKey("inbound_messages.id"))
    ack_message_id: Mapped[int | None] = mapped_column(BigInteger)
    file_id: Mapped[str] = mapped_column(Text)
    file_unique_id: Mapped[str] = mapped_column(Text)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    file_size: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[VoiceJobStatus] = mapped_column(string_enum(VoiceJobStatus, "status"))
    attempts: Mapped[int] = mapped_column(Integer)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    failure_kind: Mapped[FailureKind | None] = mapped_column(
        string_enum(FailureKind, "failure_kind")
    )
    provider: Mapped[TranscriptionProvider | None] = mapped_column(
        string_enum(TranscriptionProvider, "provider")
    )
    notify_attempts: Mapped[int] = mapped_column(Integer)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notification_outcome: Mapped[NotificationOutcome | None] = mapped_column(
        string_enum(NotificationOutcome, "notification_outcome")
    )
    notify_not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("uq_voice_jobs_telegram_ref", "telegram_chat_id", "telegram_message_id", unique=True),
        CheckConstraint("attempts BETWEEN 0 AND 5", name="attempts"),
        CheckConstraint("notify_attempts BETWEEN 0 AND 5", name="notify_attempts"),
        CheckConstraint(
            "status <> 'done' OR inbound_message_id IS NOT NULL", name="done_has_message"
        ),
        CheckConstraint("status <> 'failed' OR failure_kind IS NOT NULL", name="failed_has_kind"),
        CheckConstraint(
            "notified_at IS NULL OR status IN ('done', 'failed')", name="notified_terminal"
        ),
        CheckConstraint(
            "notify_not_before IS NULL OR status IN ('done', 'failed')", name="deferred_terminal"
        ),
        Index(
            "ix_voice_jobs_pending_last_enqueued",
            "last_enqueued_at",
            postgresql_where=text("status IN ('queued', 'processing') OR notified_at IS NULL"),
        ),
    )
