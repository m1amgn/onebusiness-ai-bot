from dataclasses import dataclass

from taskbot.domain.extraction import Extraction
from taskbot.domain.text import MessageText
from tests.fakes.calls import Calls
from tests.fakes.persistence import inside_unit_of_work


@dataclass
class FakeExtractor:
    result: Extraction
    calls: Calls

    async def extract(self, text: MessageText) -> Extraction:
        assert not inside_unit_of_work.get(), "Extraction inside a unit of work"
        self.calls.items.append("extract")
        return self.result
