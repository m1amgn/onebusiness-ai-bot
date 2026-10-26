"""Read-only replay of the synthetic owner-accepted fallback rows; no I/O services."""

import asyncio
import json

from taskbot.domain.errors import EmptyMessageError
from taskbot.domain.text import MessageText
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor

ROWS = {
    "R1": "Купить молоко",
    "R2": "Позвонить маме. Спросить про выходные и купить билеты.",
    "R3": "- купить молоко\n- позвонить маме\n- оплатить счёт",
    "R4": (
        "Подготовить к понедельнику квартальный отчёт для совета директоров с разбивкой по "
        "регионам, сравнением с прошлым годом и прогнозом на следующий квартал. Отправить Анне."
    ),
    "R5": "👍",
    "R6": "Купить молоко",
}


async def replay() -> None:
    for row_id, raw in ROWS.items():
        try:
            text = MessageText.parse(raw)
        except EmptyMessageError:
            print(json.dumps({"row": row_id, "refused": True}, ensure_ascii=False))
            continue
        result = await HeuristicExtractor(degraded=row_id == "R6").extract(text)
        print(json.dumps({"row": row_id, **result.model_dump(mode="json")}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(replay())
