import re
from datetime import UTC, datetime


def test_clock_is_aware_utc_and_tokens_are_random_secrets() -> None:
    from taskbot.infrastructure.clock import SystemClock
    from taskbot.infrastructure.tokens import SecretsTokenGenerator, Sha256TokenHasher

    before = datetime.now(UTC)
    now = SystemClock().now()
    assert before <= now <= datetime.now(UTC)
    assert now.utcoffset() == UTC.utcoffset(now)
    tokens = {SecretsTokenGenerator().generate() for _ in range(20)}
    assert len(tokens) == 20
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{43}", token) for token in tokens)
    assert Sha256TokenHasher().hash("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
