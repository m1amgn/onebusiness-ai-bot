import logging
from collections.abc import Iterable

from taskbot.domain.events import DomainEvent
from taskbot.domain.ports.messaging import EventPublisher

logger = logging.getLogger(__name__)


async def publish_events(publisher: EventPublisher, events: Iterable[DomainEvent]) -> None:
    batch = tuple(events)
    for index, event in enumerate(batch):
        try:
            await publisher.publish(event)
        except Exception:
            logger.exception(
                "Post-commit event publication failed: %s; skipped %s events",
                event.type,
                len(batch) - index - 1,
            )
            break
