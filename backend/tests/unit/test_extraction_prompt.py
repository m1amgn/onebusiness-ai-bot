import re

import pytest

from taskbot.domain.limits import DESCRIPTION_MAX, TITLE_MAX
from taskbot.infrastructure.extraction.prompt import SYSTEM_PROMPT


@pytest.mark.parametrize(
    ("cap_clause", "domain_limit"),
    [
        (r"never exceed (\d+) characters\.", TITLE_MAX),
        (r"It may use up to (\d+) characters\.", DESCRIPTION_MAX),
    ],
)
def test_prompt_cap_clause_matches_domain_limit(cap_clause: str, domain_limit: int) -> None:
    assert re.findall(cap_clause, SYSTEM_PROMPT) == [str(domain_limit)]
