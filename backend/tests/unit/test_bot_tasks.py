import pytest

from taskbot.application import replies
from tests.acceptance.replay_s10 import add_tasks
from tests.bot import HandlerHarness, message_update


async def test_first_tasks_command_registers_without_creating_a_task() -> None:
    harness = HandlerHarness()
    async with harness.bot() as bot:
        await harness.dispatcher().feed_update(bot, message_update(1, "/tasks"))
    assert [reply.text for reply in harness.notifier.replies] == [
        "You have no current tasks. Send a text or a voice message to create one."
    ]
    assert len(harness.store.state.users) == 1
    assert not harness.store.state.tasks
    assert not harness.publisher.events


@pytest.mark.parametrize(
    ("progress", "pending", "more"), [(15, 1, True), (15, 0, False), (8, 7, False), (0, 16, True)]
)
async def test_task_cap_across_status_groups(progress: int, pending: int, more: bool) -> None:
    harness = HandlerHarness()
    await add_tasks(
        harness, [f"Task {i}" for i in range(1, progress + pending + 1)], in_progress=progress
    )
    async with harness.bot() as bot:
        await harness.dispatcher().feed_update(bot, message_update(100, "/tasks@synthetic_bot"))
    text = harness.notifier.replies[0].text
    titles = [line[2:] for line in text.splitlines() if line.startswith("- ")]
    expected = [f"Task {i}" for i in range(progress, 0, -1)]
    expected += [f"Task {i}" for i in range(progress + pending, progress, -1)]
    assert titles == expected[:15]
    assert ("More tasks on your board: /dashboard" in text) is more
    assert len(harness.store.state.tasks) == progress + pending
    assert not harness.publisher.events


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/tasks", replies.NOT_PRIVATE),
        ("/tasks@synthetic_bot", replies.NOT_PRIVATE),
        ("/tasks@other_bot", None),
    ],
)
async def test_group_tasks_do_not_register(text: str, expected: str | None) -> None:
    harness = HandlerHarness()
    async with harness.bot() as bot:
        await harness.dispatcher().feed_update(bot, message_update(1, text, group=True))
    assert [item.text for item in harness.notifier.replies] == (
        [] if expected is None else [expected]
    )
    assert not harness.store.state.users


async def test_listing_database_failure_uses_the_existing_error_reply() -> None:
    harness = HandlerHarness()
    harness.store.fail_on = "tasks.page_for_user"
    async with harness.bot() as bot:
        await harness.dispatcher().feed_update(bot, message_update(1, "/tasks"))
    assert [item.text for item in harness.notifier.replies] == [replies.SOMETHING_WENT_WRONG]
