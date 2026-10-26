from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from taskbot.domain.extraction import ExtractorName
from taskbot.domain.text import MessageText


class MessageKind(StrEnum):
    TEXT = "text"
    VOICE = "voice"


@dataclass(frozen=True, slots=True)
class MessageRef:
    chat_id: int
    message_id: int


@dataclass(frozen=True, slots=True)
class NewInboundMessage:
    user_id: int
    ref: MessageRef
    kind: MessageKind
    text: MessageText
    extractor: ExtractorName
    degraded: bool


@dataclass(frozen=True, slots=True)
class InboundMessage:
    id: int
    user_id: int
    ref: MessageRef
    kind: MessageKind
    text: str | None
    extractor: ExtractorName
    degraded: bool
    created_at: datetime
