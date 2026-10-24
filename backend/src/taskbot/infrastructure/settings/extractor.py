from typing import Literal

from pydantic import BaseModel, SecretStr


class ExtractorSettingsMixin(BaseModel):
    task_extractor: Literal["heuristic", "claude"] = "heuristic"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-haiku-5-5"
