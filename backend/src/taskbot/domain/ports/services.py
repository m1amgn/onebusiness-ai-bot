from datetime import datetime
from typing import Protocol

from taskbot.domain.extraction import Extraction
from taskbot.domain.text import MessageText


class Clock(Protocol):
    def now(self) -> datetime: ...


class TokenGenerator(Protocol):
    def generate(self) -> str: ...


class TokenHasher(Protocol):
    def hash(self, token: str) -> str: ...


class TaskExtractor(Protocol):
    async def extract(self, text: MessageText) -> Extraction: ...
