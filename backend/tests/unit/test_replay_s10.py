import json
import socket
from pathlib import Path
from typing import Never

import pytest

from tests.acceptance.replay_s10 import ROWS, run_rows, run_scenario


async def test_owner_acceptance_rows_match_frozen_results() -> None:
    expected = json.loads(
        (Path(__file__).parents[1] / "acceptance" / "s10_expected.json").read_text()
    )
    assert await run_rows() == expected


async def test_replay_stays_offline_with_inherited_provider_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse_network(*args: object, **kwargs: object) -> Never:
        raise AssertionError("The S10 replay must not connect to any service")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-inherited-key")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-inherited-key")
    monkeypatch.setenv("TASK_EXTRACTOR", "claude")
    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    assert len(await run_rows()) == 10


@pytest.mark.parametrize("row", ROWS)
async def test_owner_rows_keep_reply_target_and_registration_boundaries(row: str) -> None:
    harness = await run_scenario(row)
    assert len(harness.notifier.replies) == 1
    assert harness.notifier.replies[0].reply_to_message_id == 100
    assert len(harness.store.state.users) == (
        1 if row in {"T1", "T2", "T3", "T4", "T5", "T6", "T8"} else 0
    )
    assert not harness.publisher.events
    assert all(user.token_version == 0 for user in harness.store.state.users.values())
    if row == "T1":
        assert not harness.store.state.messages
        assert not harness.store.state.tasks
    if row == "T8":
        assert len(harness.queue.jobs) == len(harness.store.state.voice_jobs) == 1
