"""Offline owner acceptance replay. Uses only synthetic messages and in-memory stores."""

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.types import Update

from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor

# A directly executed replay needs the backend tests package on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.bot import TOKEN, HandlerHarness, bot_settings
from tests.bot import message_update as message
from tests.fakes.telegram_session import FakeTelegramSession


@dataclass(frozen=True)
class Row:
    name: str
    updates: tuple[Update, ...]
    keyless: bool = False
    degraded: bool = False


def acceptance_updates() -> list[Row]:
    first = message(1, text="Buy milk")
    voice = message(4, duration=35)
    edited = message(1, text="Buy oat milk").message
    return [
        Row("B1", (first,)),
        Row("B2", (message(2, text="Call the bank about the card"),), degraded=True),
        Row("B3", (message(3, text="🙂👍"),)),
        Row("B4", (voice,)),
        Row("B5", (message(5, duration=150),)),
        Row("B6", (message(6, duration=30, size=12_000_000),)),
        Row("B7", (message(7, duration=30, size=None),)),
        Row("B8", (voice,)),
        Row("B9", (first,)),
        Row("B10", (message(10, text="/dashboard"),)),
        Row("B11", (message(11, text="/dashboard", group=True),)),
        Row("B12", (message(12, text="Buy milk", group=True),)),
        Row("B13", (message(13, sticker=True),)),
        Row("B14", (message(14, text="/help"),)),
        Row("B15", (Update(update_id=15, edited_message=edited),)),
        Row("B16", (message(16, text="Buy bread", forwarded=True),)),
        Row("B17", (message(17, duration=20, caption="/dashboard"),)),
        Row("B18", (message(18, text="/start"),)),
        Row("B19", (message(19, text="/start"),), keyless=True, degraded=True),
        # Supplementary inputs pin the text/voice behaviour described beside B19.
        Row("B19b", (message(20, text="Buy eggs"),), keyless=True, degraded=True),
        Row("B19c", (message(21, duration=20),), keyless=True, degraded=True),
    ]


async def run_rows() -> list[dict[str, object]]:
    h = HandlerHarness()
    store, notifier, queue, publisher = h.store, h.notifier, h.queue, h.publisher
    rows: list[dict[str, object]] = []
    async with Bot(TOKEN, session=FakeTelegramSession()) as bot:
        for row in acceptance_updates():
            before = (
                len(notifier.replies),
                len(queue.jobs),
                len(publisher.events),
                set(store.state.messages),
                set(store.state.tasks),
                set(store.state.voice_jobs),
            )
            versions = sum(user.token_version for user in store.state.users.values())
            h.settings = bot_settings(keyless=row.keyless)
            h.extractor = HeuristicExtractor(degraded=row.degraded)
            dispatcher = h.dispatcher()
            for update in row.updates:
                await dispatcher.feed_update(bot, update)
            new_messages = [
                item for key, item in store.state.messages.items() if key not in before[3]
            ]
            new_tasks = [item for key, item in store.state.tasks.items() if key not in before[4]]
            rows.append(
                {
                    "row": row.name,
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
                        event.task.source_text
                        for event in publisher.events[before[2] :]
                        if event.type == "task.created"
                    ],
                    "reply_to": [
                        item.reply_to_message_id for item in notifier.replies[before[0] :]
                    ],
                    "accepted_voice": [
                        {"status": str(item.status), "ack_message_id": item.ack_message_id}
                        for key, item in store.state.voice_jobs.items()
                        if key not in before[5]
                    ],
                    "token_rotated": sum(user.token_version for user in store.state.users.values())
                    > versions,
                }
            )
            if row.name == "B15":
                assert store.state.tasks[1].title == "Buy milk"
            if row.name == "B16":
                assert store.state.users[new_tasks[0].user_id].telegram_user_id == 7
    return rows


async def main() -> None:
    for row in await run_rows():
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
