from pydantic import AnyHttpUrl, BaseModel, field_validator


class WebSettingsMixin(BaseModel):
    web_base_url: str = "http://localhost:5173"

    @field_validator("web_base_url")
    @classmethod
    def validate_web_url(cls, value: str) -> str:
        AnyHttpUrl(value)
        return value.rstrip("/")
