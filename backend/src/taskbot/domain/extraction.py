from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from taskbot.domain.limits import DESCRIPTION_MAX, TITLE_MAX
from taskbot.domain.text import clean_inline, clean_text


class ExtractorName(StrEnum):
    HEURISTIC = "heuristic"
    CLAUDE = "claude"


class TaskDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    title: Annotated[str, Field(min_length=1, max_length=TITLE_MAX)]
    description: Annotated[str, Field(max_length=DESCRIPTION_MAX)] = ""

    @field_validator("title", mode="before")
    @classmethod
    def clean_title(cls, value: object) -> object:
        return clean_inline(value) if isinstance(value, str) else value

    @field_validator("description", mode="before")
    @classmethod
    def clean_description(cls, value: object) -> object:
        return clean_text(value) if isinstance(value, str) else value


class Extraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    task: TaskDraft
    extractor: ExtractorName
    degraded: bool
