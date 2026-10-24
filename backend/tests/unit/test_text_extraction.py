import random

import pytest
from pydantic import ValidationError


@pytest.mark.parametrize("raw", ["", " \t\n", "👍", "...", "\u200b", "\x00"])
def test_non_actionable_text_is_refused(raw: str) -> None:
    from taskbot.domain.errors import EmptyMessageError
    from taskbot.domain.text import MessageText

    with pytest.raises(EmptyMessageError, match="letter or digit"):
        MessageText.parse(raw)


def test_cleaning_preserves_breaks_and_removes_unstorable_characters() -> None:
    from taskbot.domain.text import MessageText, clean_inline, clean_text

    assert clean_text(" a\r\nb\rc\vd\fe\x85f\u2028g\u2029h\x00\ud800 ") == (
        "a\nb\nc\nd\ne\nf\ng\nh"
    )
    assert clean_inline(" a \n\t b ") == "a b"
    assert MessageText.parse(" Купить 🥛 ").value == "Купить 🥛"
    assert MessageText.parse("²").value == "²"


@pytest.mark.parametrize(
    ("raw", "title", "description"),
    [
        ("Купить молоко", "Купить молоко", ""),
        ("Позвонить маме. Спросить про выходные.", "Позвонить маме. Спросить про выходные.", ""),
        ("- buy milk\n- call home", "- buy milk - call home", "- buy milk\n- call home"),
        ("a" * 120, "a" * 120, ""),
        ("a" * 121, "a" * 119 + "…", "a" * 121),
        ("界" * 130, "界" * 119 + "…", "界" * 130),
        ("a" * 3000, "a" * 119 + "…", "a" * 1999 + "…"),
    ],
)
async def test_fallback_keeps_one_task(raw: str, title: str, description: str) -> None:
    from taskbot.domain.text import MessageText
    from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor

    result = await HeuristicExtractor(degraded=True).extract(MessageText.parse(raw))
    assert len(result.tasks) == 1
    assert result.tasks[0].title == title
    assert result.tasks[0].description == description
    assert result.extractor == "heuristic"
    assert result.degraded


async def test_fallback_is_total_on_accepted_hostile_text() -> None:
    from taskbot.domain.errors import EmptyMessageError
    from taskbot.domain.text import MessageText
    from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor

    rng = random.Random(20261024)
    alphabet = "abcАБВ中文123 .!?-\n\t\r\x00\ud800\u200b👍"
    extractor = HeuristicExtractor()
    for _ in range(3000):
        raw = "".join(rng.choices(alphabet, k=rng.randrange(500)))
        try:
            text = MessageText.parse(raw)
        except EmptyMessageError:
            continue
        result = await extractor.extract(text)
        assert len(result.tasks) == 1
        assert 1 <= len(result.tasks[0].title) <= 120
        assert len(result.tasks[0].description) <= 2000


def test_drafts_and_extraction_validate_their_boundary() -> None:
    from taskbot.domain.extraction import ExtractedTasks, Extraction, ExtractorName, TaskDraft

    draft = TaskDraft(title=" a\x00\n b ", description="c\ud800")
    assert draft.title == "a b"
    assert draft.description == "c"
    for title in ("", "a" * 121):
        with pytest.raises(ValidationError, match="title"):
            TaskDraft(title=title)
    with pytest.raises(ValidationError, match="tasks"):
        ExtractedTasks(tasks=[])
    with pytest.raises(ValidationError, match="tasks"):
        Extraction(tasks=(), extractor=ExtractorName.HEURISTIC, degraded=False)


async def test_factory_reports_unbuilt_model_as_degraded() -> None:
    from taskbot.domain.text import MessageText
    from taskbot.infrastructure.extraction.factory import build_extractor
    from taskbot.infrastructure.settings.extractor import ExtractorSettingsMixin

    for requested, degraded in (("heuristic", False), ("claude", True)):
        settings = ExtractorSettingsMixin.model_validate({"task_extractor": requested})
        result = await build_extractor(settings).extract(MessageText.parse("call home"))
        assert result.degraded is degraded
