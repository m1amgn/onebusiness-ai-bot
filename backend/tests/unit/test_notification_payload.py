from datetime import UTC, datetime

import pytest

from taskbot.domain.jobs import NotificationJob
from taskbot.domain.messages import MessageRef
from taskbot.domain.voice_jobs import UnnotifiedJob, VoiceJob, VoiceJobStatus


@pytest.mark.parametrize("status", [VoiceJobStatus.DONE, VoiceJobStatus.FAILED])
@pytest.mark.parametrize("projection", [False, True])
def test_notification_payload_uses_the_persisted_terminal_state(
    status: VoiceJobStatus, projection: bool
) -> None:
    now = datetime(2026, 10, 27, tzinfo=UTC)
    row = VoiceJob(7, 3, MessageRef(3, 4), "file", "unique", 5, None, now, now, now, status=status)
    source = UnnotifiedJob(row.id, row.status) if projection else row
    assert NotificationJob.from_voice_job(source).model_dump() == {
        "voice_job_id": 7,
        "status": status.value,
    }


@pytest.mark.parametrize("status", [VoiceJobStatus.QUEUED, VoiceJobStatus.PROCESSING])
def test_notification_payload_refuses_nonterminal_state(status: VoiceJobStatus) -> None:
    now = datetime(2026, 10, 27, tzinfo=UTC)
    row = VoiceJob(7, 3, MessageRef(3, 4), "file", "unique", 5, None, now, now, now, status=status)
    with pytest.raises(ValueError, match="terminal"):
        NotificationJob.from_voice_job(row)
