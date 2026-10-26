from datetime import datetime


def require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
