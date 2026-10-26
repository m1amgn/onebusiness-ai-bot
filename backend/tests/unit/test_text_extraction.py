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
        (
            "Позвонить маме. Спросить про выходные и купить билеты.",
            "Позвонить маме. Спросить про выходные и купить билеты.",
            "",
        ),
        (
            "- купить молоко\n- позвонить маме\n- оплатить счёт",
            "- купить молоко - позвонить маме - оплатить счёт",
            "- купить молоко\n- позвонить маме\n- оплатить счёт",
        ),
        (
            "Подготовить к понедельнику квартальный отчёт для совета директоров с разбивкой по "
            "регионам, сравнением с прошлым годом и прогнозом на следующий квартал. "
            "Отправить Анне.",
            "Подготовить к понедельнику квартальный отчёт для совета директоров с разбивкой по "
            "регионам, сравнением с прошлым годом…",
            "Подготовить к понедельнику квартальный отчёт для совета директоров с разбивкой по "
            "регионам, сравнением с прошлым годом и прогнозом на следующий квартал. "
            "Отправить Анне.",
        ),
        ("a" * 120, "a" * 120, ""),
        ("a" * 121, "a" * 119 + "…", "a" * 121),
        ("word " * 24 + "x", "word " * 22 + "word…", "word " * 24 + "x"),
        ("界" * 130, "界" * 119 + "…", "界" * 130),
        ("a" * 5000, "a" * 119 + "…", "a" * 4095 + "…"),
    ],
    ids=[
        "short",
        "sentence",
        "list",
        "long",
        "title-limit",
        "title-over",
        "words",
        "cjk",
        "description-over",
    ],
)
async def test_fallback_keeps_one_task(raw: str, title: str, description: str) -> None:
    from taskbot.domain.text import MessageText
    from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor

    result = await HeuristicExtractor(degraded=True).extract(MessageText.parse(raw))
    assert result.task.title == title
    assert result.task.description == description
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
        assert 1 <= len(result.task.title) <= 120
        assert len(result.task.description) <= 4096


def test_drafts_and_extraction_validate_their_boundary() -> None:
    from taskbot.domain.extraction import Extraction, ExtractorName, TaskDraft

    draft = TaskDraft(title=" a\x00\n b ", description="c\ud800")
    assert draft.title == "a b"
    assert draft.description == "c"
    assert len(TaskDraft(title="long", description="я" * 4096).description) == 4096
    with pytest.raises(ValidationError, match="description"):
        TaskDraft(title="long", description="я" * 4097)
    for title in ("", "a" * 121):
        with pytest.raises(ValidationError, match="title"):
            TaskDraft(title=title)
    extraction = Extraction(task=draft, extractor=ExtractorName.HEURISTIC, degraded=False)
    assert extraction.task == draft
    for invalid in ({}, {"tasks": (draft,)}, {"task": [draft]}):
        with pytest.raises(ValidationError):
            Extraction.model_validate({"extractor": "heuristic", "degraded": False, **invalid})


async def test_factory_selects_the_configured_fallback() -> None:
    from taskbot.domain.text import MessageText
    from taskbot.infrastructure.extraction.factory import open_extractor
    from taskbot.infrastructure.settings.extractor import ExtractorSettingsMixin

    for requested, degraded in (("heuristic", False), ("claude", True)):
        settings = ExtractorSettingsMixin.model_validate({"task_extractor": requested})
        async with open_extractor(settings, deadline_seconds=1) as extractor:
            result = await extractor.extract(MessageText.parse("call home"))
        assert result.degraded is degraded
