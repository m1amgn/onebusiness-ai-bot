import pytest

from taskbot.application import list_tasks
from taskbot.domain.tasks import SourcedTask, TaskCursor, TaskStatus
from tests.acceptance.replay_s10 import add_tasks
from tests.bot import HandlerHarness
from tests.fakes.persistence import FakeTaskRepository


@pytest.mark.parametrize(
    ("progress", "pending", "completed", "shown_progress", "shown_pending", "more", "fetches"),
    [
        (0, 0, 0, 0, 0, False, (16, 16)),
        (0, 0, 3, 0, 0, False, (16, 16)),
        (3, 2, 1, 3, 2, False, (16, 13)),
        (15, 1, 0, 15, 0, True, (16, 2)),
        (15, 0, 0, 15, 0, False, (16, 2)),
        (16, 0, 0, 15, 0, True, (16, 2)),
        (8, 7, 0, 8, 7, False, (16, 8)),
        (0, 16, 0, 0, 15, True, (16, 16)),
    ],
)
async def test_current_tasks_statuses_cap_order_and_canonical_reads(
    progress: int,
    pending: int,
    completed: int,
    shown_progress: int,
    shown_pending: int,
    more: bool,
    fetches: tuple[int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = HandlerHarness()
    # Completed tasks occupy the first positions after the in-progress group.
    await add_tasks(
        harness,
        [f"Task {i}" for i in range(1, progress + pending + completed + 1)],
        in_progress=progress,
        completed=completed,
    )
    calls: list[tuple[int, TaskStatus | None, TaskCursor | None, int]] = []
    original = FakeTaskRepository.page_for_user

    async def page(
        self: FakeTaskRepository,
        user_id: int,
        *,
        status: TaskStatus | None,
        after: TaskCursor | None,
        fetch: int,
    ) -> tuple[SourcedTask, ...]:
        calls.append((user_id, status, after, fetch))
        return await original(self, user_id, status=status, after=after, fetch=fetch)

    monkeypatch.setattr(FakeTaskRepository, "page_for_user", page)
    current = await list_tasks.ListCurrentTasks(list_tasks.ListTasks(harness.store)).execute(1)
    assert len(current.in_progress) == shown_progress
    assert len(current.pending) == shown_pending
    assert current.more is more
    assert [task.title for task in current.in_progress] == [
        f"Task {i}" for i in range(progress, progress - shown_progress, -1)
    ]
    assert [task.title for task in current.pending] == [
        f"Task {i}"
        for i in range(
            progress + pending + completed, progress + pending + completed - shown_pending, -1
        )
    ]
    assert calls == [
        (1, TaskStatus.IN_PROGRESS, None, fetches[0]),
        (1, TaskStatus.PENDING, None, fetches[1]),
    ]


async def test_current_tasks_never_returns_another_users_tasks() -> None:
    harness = HandlerHarness()
    await add_tasks(harness, ["Foreign task"], in_progress=1)
    result = await list_tasks.ListCurrentTasks(list_tasks.ListTasks(harness.store)).execute(999)
    assert result.in_progress == result.pending == ()
    assert result.more is False
