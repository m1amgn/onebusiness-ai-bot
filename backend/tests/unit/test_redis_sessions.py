import pytest

from tests.redis_sessions import candidate_id


@pytest.mark.parametrize(
    "key",
    [
        "taskbot_test_x:k",
        "taskbot_test_1_ab12cd34",
        "transcription",
        "unacked",
        "_kombu.binding.transcription",
        "events:user:1",
    ],
)
def test_candidate_parser_refuses_unowned_keys(key: str) -> None:
    assert candidate_id(key) is None


def test_candidate_parser_accepts_session_child_key() -> None:
    assert candidate_id("taskbot_test_1_ab12cd34:t2:transcription") == "taskbot_test_1_ab12cd34"
