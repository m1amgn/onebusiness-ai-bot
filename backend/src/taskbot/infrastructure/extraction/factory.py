import logging

from taskbot.domain.ports.services import TaskExtractor
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.settings.extractor import ExtractorSettingsMixin


def build_extractor(settings: ExtractorSettingsMixin) -> TaskExtractor:
    if settings.task_extractor == "heuristic":
        return HeuristicExtractor()
    logging.getLogger(__name__).warning("Claude extractor is not built; using degraded fallback")
    return HeuristicExtractor(degraded=True)
