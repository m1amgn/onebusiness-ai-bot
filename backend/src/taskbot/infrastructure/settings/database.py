from pydantic import BaseModel, PostgresDsn, field_validator


class DatabaseSettingsMixin(BaseModel):
    database_url: PostgresDsn

    @field_validator("database_url")
    @classmethod
    def require_asyncpg(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme != "postgresql+asyncpg":
            raise ValueError("database_url must use postgresql+asyncpg")
        return value
