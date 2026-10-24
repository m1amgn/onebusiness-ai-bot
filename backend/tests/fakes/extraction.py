from dataclasses import dataclass

from taskbot.domain.extraction import Extraction
from taskbot.domain.text import MessageText
from tests.fakes.calls import Calls


@dataclass
class FakeExtractor:
    result: Extraction
    calls: Calls

    async def extract(self, text: MessageText) -> Extraction:
        self.calls.items.append("extract")
        return self.result
