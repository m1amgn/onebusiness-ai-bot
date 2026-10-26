"""Offline owner acceptance replay. Uses only synthetic messages and in-memory stores."""

import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot
from aiogram.types import (
    Chat,
    Message,
    MessageOriginHiddenUser,
    Sticker,
    Update,
    User,
    Voice,
)
from pydantic import PostgresDsn, SecretStr

from taskbot.domain.voice_jobs import TranscriptionProvider
from taskbot.entrypoints.bot import build_bot_ports
from taskbot.entrypoints.bot.handlers import create_dispatcher
from taskbot.entrypoints.bot.runtime import InFlightUpdates
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.settings.bot import BotSettings

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def settings(*, keyless: bool = False) -> BotSettings:
    return BotSettings(
        _env_file=None,
        database_url=PostgresDsn("postgresql+asyncpg://localhost/synthetic"),
        telegram_bot_token=SecretStr("123456:synthetic"),
        transcription_provider=TranscriptionProvider.OPENAI
        if keyless
        else TranscriptionProvider.WHISPER_SERVER,
        task_extractor="claude" if keyless else "heuristic",
        anthropic_api_key=None,
        openai_api_key=None,
        web_base_url="http://localhost:5173",
    )


def message(
    number: int,
    *,
    text: str | None = None,
    duration: int | None = None,
    size: int | None = 100,
    group: bool = False,
    caption: str | None = None,
    sticker: bool = False,
    forwarded: bool = False,
) -> Update:
    item = Message(
        message_id=number,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        chat=Chat(id=7, type="group" if group else "private"),
        from_user=User(id=7, is_bot=False, first_name="Synthetic"),
        text=text,
        caption=caption,
        voice=None
        if duration is None
        else Voice(file_id="file", file_unique_id="unique", duration=duration, file_size=size),
        sticker=Sticker(
            file_id="sticker",
            file_unique_id="unique",
            type="regular",
            width=10,
            height=10,
            is_animated=False,
            is_video=False,
        )
        if sticker
        else None,
        forward_origin=MessageOriginHiddenUser(
            date=datetime(2026, 1, 1, tzinfo=UTC), sender_user_name="Synthetic"
        )
        if forwarded
        else None,
    )
    return Update(update_id=number, message=item)


def acceptance_updates() -> list[tuple[str, Update]]:
    first = message(1, text="Buy milk")
    voice = message(4, duration=35)
    edited = message(1, text="Buy oat milk").message
    return [
        ("B1", first),
        ("B2", message(2, text="Call the bank about the card")),
        ("B3", message(3, text="🙂👍")),
        ("B4", voice),
        ("B5", message(5, duration=150)),
        ("B6", message(6, duration=30, size=12_000_000)),
        ("B7", message(7, duration=30, size=None)),
        ("B8", voice),
        ("B9", first),
        ("B10", message(10, text="/dashboard")),
        ("B11", message(11, text="/dashboard", group=True)),
        ("B12", message(12, text="Buy milk", group=True)),
        ("B13", message(13, sticker=True)),
        ("B14", message(14, text="/help")),
        ("B15", Update(update_id=15, edited_message=edited)),
        ("B16", message(16, text="Buy bread", forwarded=True)),
        ("B17", message(17, duration=20, caption="/dashboard")),
        ("B18", message(18, text="/start")),
        ("B19", message(19, text="/start")),
    ]


async def run_rows() -> list[dict[str, object]]:
    sys.path.insert(0, str(BACKEND_ROOT))
    from tests.fakes.messaging import FakeEventPublisher, FakeJobQueue
    from tests.fakes.persistence import FakeStore
    from tests.fakes.telegram import FakeUserNotifier
    from tests.fakes.telegram_session import FakeTelegramSession

    store = FakeStore()
    notifier = FakeUserNotifier(calls=store.calls)
    queue = FakeJobQueue(calls=store.calls)
    publisher = FakeEventPublisher(calls=store.calls)
    rows: list[dict[str, object]] = []
    async with Bot("123456:synthetic", session=FakeTelegramSession()) as bot:
        for name, update in acceptance_updates():
            before = (
                len(notifier.replies),
                len(queue.jobs),
                len(publisher.events),
                set(store.state.messages),
                set(store.state.tasks),
                set(store.state.voice_jobs),
            )
            versions = sum(user.token_version for user in store.state.users.values())
            ports = build_bot_ports(
                settings(keyless=name == "B19"),
                store,
                HeuristicExtractor(degraded=name in {"B2", "B19"}),
                notifier,
                queue,
                publisher,
            )
            await create_dispatcher(ports, InFlightUpdates()).feed_update(bot, update)
            new_messages = [
                item for key, item in store.state.messages.items() if key not in before[3]
            ]
            new_tasks = [item for key, item in store.state.tasks.items() if key not in before[4]]
            rows.append(
                {
                    "row": name,
                    "replies": [
                        re.sub(r"#token=[A-Za-z0-9_-]+", "#token=<token>", item.text)
                        for item in notifier.replies[before[0] :]
                    ],
                    "messages": len(new_messages),
                    "tasks": len(new_tasks),
                    "voice_jobs": len(set(store.state.voice_jobs) - before[5]),
                    "jobs": len(queue.jobs) - before[1],
                    "events": [item.type for item in publisher.events[before[2] :]],
                    "degraded": [item.degraded for item in new_messages],
                    "source_texts": [
                        store.state.messages[item.inbound_message_id].text for item in new_tasks
                    ],
                    "token_rotated": sum(user.token_version for user in store.state.users.values())
                    > versions,
                }
            )
            if name == "B15":
                assert store.state.tasks[1].title == "Buy milk"
            if name == "B16":
                assert store.state.users[new_tasks[0].user_id].telegram_user_id == 7
    return rows


async def main() -> None:
    for row in await run_rows():
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
