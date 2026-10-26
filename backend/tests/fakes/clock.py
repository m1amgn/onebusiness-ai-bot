from dataclasses import dataclass
from datetime import datetime, timedelta

from taskbot.domain.time import require_aware


@dataclass
class FakeClock:
    value: datetime

    def now(self) -> datetime:
        require_aware(self.value)
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)
