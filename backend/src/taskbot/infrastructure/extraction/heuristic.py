from taskbot.domain.extraction import Extraction, ExtractorName, TaskDraft
from taskbot.domain.limits import DESCRIPTION_MAX, TITLE_MAX
from taskbot.domain.text import MessageText, clean_inline


class HeuristicExtractor:
    def __init__(self, degraded: bool = False) -> None:
        self._degraded = degraded

    async def extract(self, text: MessageText) -> Extraction:
        line = clean_inline(text.value)
        title = line
        if len(line) > TITLE_MAX:
            prefix = line[: TITLE_MAX - 1]
            space = prefix.rfind(" ")
            title = (prefix[:space] if space > 0 else prefix) + "…"
        description = "" if title == text.value else text.value
        if len(description) > DESCRIPTION_MAX:
            description = description[: DESCRIPTION_MAX - 1] + "…"
        return Extraction(
            tasks=(TaskDraft(title=title, description=description),),
            extractor=ExtractorName.HEURISTIC,
            degraded=self._degraded,
        )
