"""Read-only owner replay: synthetic inputs, guarded providers, no external I/O."""

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx

from taskbot.application.create_tasks import CreateTasksFromMessage
from taskbot.application.delete_task import DeleteTask
from taskbot.application.notify_user import NotifyUser
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.application.transcribe_voice import AttemptProgress, TranscribeVoiceJob
from taskbot.domain.errors import (
    RecipientBlockedError,
    TranscriptionError,
    TransientTranscriptionError,
)
from taskbot.domain.jobs import TranscriptionJob
from taskbot.domain.ports.transcription import Transcriber, Transcript
from taskbot.domain.voice_jobs import FailureKind, TranscriptionProvider
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.transcription.fake import UnavailableTranscriber
from taskbot.infrastructure.transcription.whisper_server import WhisperServerTranscriber

# Direct script execution must resolve the same tests package as pytest.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.audio import synthetic_opus
from tests.fakes.clock import FakeClock
from tests.fakes.messaging import FakeEventPublisher, FakeJobQueue
from tests.fakes.persistence import FakeStore
from tests.fakes.telegram import FakeUserNotifier, FakeVoiceFileSource
from tests.fakes.transcription import ScriptedTranscriber
from tests.s5 import FIXTURE, SENTENCE, create_job


@asynccontextmanager
async def case_transcriber(row: str) -> AsyncIterator[Transcriber]:
    if row == "V3":
        yield UnavailableTranscriber()
        return
    if row in {"V5", "V6"}:
        requests: list[httpx.Request] = []

        def response(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if row == "V5":
                assert b"not an Ogg stream" in request.content
                return httpx.Response(500, text="FFmpeg conversion failed.")
            return httpx.Response(200, json={"text": "[BLANK_AUDIO]"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(response), base_url="http://synthetic"
        ) as client:
            yield WhisperServerTranscriber(client)
        assert len(requests) == 1
        return
    provider = TranscriptionProvider.WHISPER_SERVER
    script: list[Transcript | BaseException]
    if row == "V7":
        script = [TransientTranscriptionError() for _ in range(5)]
    elif row == "V8":
        script = [TranscriptionError(FailureKind.TRANSCRIPTION_TIMEOUT)]
    else:
        script = [Transcript("call supplier " * 400 if row == "V12" else SENTENCE, provider)]
    yield ScriptedTranscriber(script, provider=provider)


async def run_row(row_id: str) -> dict[str, object]:
    store = FakeStore()
    clock = FakeClock(datetime(2026, 10, 27, tzinfo=UTC))
    row = await create_job(store, clock, ack=row_id != "V10", duration=100 if row_id == "V4" else 5)
    audio = synthetic_opus(125, granule=240000) if row_id == "V4" else FIXTURE.read_bytes()
    if row_id == "V5":
        audio = b"not an Ogg stream"
    extractor = HeuristicExtractor(degraded=row_id == "V2")
    notifier = FakeUserNotifier()
    if row_id == "V11":
        notifier.failures[1] = RecipientBlockedError("synthetic block")
    queue = FakeJobQueue()
    create = CreateTasksFromMessage(store, extractor, RegisterOrGetUser(store, clock), clock)
    notify = NotifyUser(store, notifier, queue, clock)
    async with case_transcriber(row_id) as transcriber:
        use_case = TranscribeVoiceJob(
            store,
            create,
            FakeVoiceFileSource(audio),
            transcriber,
            extractor,
            notifier,
            queue,
            FakeEventPublisher(),
            clock,
        )
        await use_case.execute(TranscriptionJob(voice_job_id=row.id), AttemptProgress())
        while queue.jobs:
            job, delay = queue.jobs.pop(0)
            clock.advance(delay)
            if isinstance(job, TranscriptionJob):
                await use_case.execute(job, AttemptProgress())
                continue
            if row_id == "V9":
                task = next(iter(store.state.tasks.values()))
                await DeleteTask(store).execute(row.user_id, task.id)
            await notify.execute(job)
    final = store.state.voice_jobs[row.id]
    assert final.notification_outcome is not None
    result: dict[str, object] = {
        "row": row_id,
        "status": final.status.value,
        "outcome": final.notification_outcome.value,
        "chat": [text for _, _, text in notifier.edits]
        + [reply.text for reply in notifier.replies],
        "tasks": len(store.state.tasks),
    }
    if row_id == "V10":
        result["reply_to"] = notifier.replies[0].reply_to_message_id
    if row_id == "V12":
        assert final.inbound_message_id is not None
        text = store.state.messages[final.inbound_message_id].text
        assert text is not None
        result["source_length"] = len(text)
    return result


async def run_rows() -> list[dict[str, object]]:
    with (
        patch(
            "taskbot.infrastructure.transcription.factory.open_transcriber",
            side_effect=AssertionError("Live transcriber factory reached offline replay"),
        ),
        patch(
            "taskbot.infrastructure.extraction.factory.open_extractor",
            side_effect=AssertionError("Live extractor factory reached offline replay"),
        ),
        patch(
            "socket.socket.connect", side_effect=AssertionError("Network reached offline replay")
        ),
    ):
        return [await run_row(f"V{index}") for index in range(1, 13)]


async def main() -> None:
    for row in await run_rows():
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
