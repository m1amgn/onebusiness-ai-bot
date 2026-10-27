from dataclasses import dataclass, field

from tests.fakes.calls import Calls
from tests.fakes.persistence import inside_unit_of_work


@dataclass(frozen=True, slots=True)
class Reply:
    chat_id: int
    text: str
    reply_to_message_id: int | None
    message_id: int


@dataclass
class FakeUserNotifier:
    calls: Calls = field(default_factory=Calls)
    replies: list[Reply] = field(default_factory=list)
    edits: list[tuple[int, int, str]] = field(default_factory=list)
    failures: dict[int, Exception] = field(default_factory=dict)
    count: int = 0

    def _check(self, operation: str) -> None:
        assert not inside_unit_of_work.get(), "Telegram call inside a unit of work"
        self.calls.items.append(operation)
        self.count += 1
        if self.count in self.failures:
            raise self.failures[self.count]

    async def send_reply(
        self, chat_id: int, text: str, *, reply_to_message_id: int | None = None
    ) -> int:
        self._check("notifier.send_reply")
        self.replies.append(Reply(chat_id, text, reply_to_message_id, self.count))
        return self.count

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        self._check("notifier.edit_message")
        self.edits.append((chat_id, message_id, text))


@dataclass
class FakeVoiceFileSource:
    data: bytes = b"synthetic audio"
    calls: Calls = field(default_factory=Calls)
    failures: dict[int, BaseException] = field(default_factory=dict)
    requests: list[tuple[str, int]] = field(default_factory=list)

    async def fetch(self, file_id: str, *, max_bytes: int) -> bytes:
        assert not inside_unit_of_work.get(), "Download inside a unit of work"
        self.calls.items.append("files.fetch")
        self.requests.append((file_id, max_bytes))
        if len(self.requests) in self.failures:
            raise self.failures[len(self.requests)]
        return self.data
