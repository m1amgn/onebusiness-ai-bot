from pydantic import TypeAdapter
from sqlalchemy import RowMapping

from taskbot.domain.messages import InboundMessage
from taskbot.domain.tasks import SourcedTask, Task
from taskbot.domain.users import User
from taskbot.domain.voice_jobs import VoiceJob

USER = TypeAdapter(User)
MESSAGE = TypeAdapter(InboundMessage)
TASK = TypeAdapter(Task)
VOICE_JOB = TypeAdapter(VoiceJob)


def user_from_row(row: RowMapping) -> User:
    # Core mappings are untyped in SQLAlchemy; validate them at the storage boundary.
    return USER.validate_python(dict(row))


def with_ref(row: RowMapping) -> dict[str, object]:
    values: dict[str, object] = {str(key): value for key, value in row.items()}
    values["ref"] = {
        "chat_id": values.pop("telegram_chat_id"),
        "message_id": values.pop("telegram_message_id"),
    }
    return values


def message_from_row(row: RowMapping) -> InboundMessage:
    return MESSAGE.validate_python(with_ref(row))


def task_from_row(row: RowMapping) -> Task:
    return TASK.validate_python(dict(row))


def voice_job_from_row(row: RowMapping) -> VoiceJob:
    return VOICE_JOB.validate_python(with_ref(row))


def sourced_task_from_row(row: RowMapping) -> SourcedTask:
    source = row["source_text"]
    if not isinstance(source, str):
        raise RuntimeError("Task has no source message text")
    return SourcedTask(task_from_row(row), source)
