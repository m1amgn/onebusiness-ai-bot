import unicodedata
from dataclasses import dataclass

from taskbot.domain.errors import EmptyMessageError


def clean_text(raw: str) -> str:
    normalized = raw.replace("\r\n", "\n")
    for separator in ("\r", "\v", "\f", "\x85", "\u2028", "\u2029"):
        normalized = normalized.replace(separator, "\n")
    return "".join(
        ch for ch in normalized if ch in "\n\t" or unicodedata.category(ch) not in {"Cc", "Cs"}
    ).strip()


def clean_inline(raw: str) -> str:
    return " ".join(clean_text(raw).split())


@dataclass(frozen=True, slots=True)
class MessageText:
    value: str

    def bounded(self, limit: int) -> tuple[MessageText, bool]:
        if limit < 1:
            raise ValueError("Text limit must be positive")
        if len(self.value) <= limit:
            return self, False
        prefix = self.value[:limit]
        boundary = max((index for index, char in enumerate(prefix) if char.isspace()), default=-1)
        return self.parse(prefix[:boundary] if boundary > 0 else prefix), True

    @classmethod
    def parse(cls, raw: str) -> MessageText:
        cleaned = clean_text(raw)
        if not any(ch.isalnum() for ch in cleaned):
            raise EmptyMessageError("Message must contain a letter or digit")
        return cls(cleaned)
