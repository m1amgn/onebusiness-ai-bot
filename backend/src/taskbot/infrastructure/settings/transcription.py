from pydantic import BaseModel, SecretStr

from taskbot.domain.voice_jobs import TranscriptionProvider


class TranscriptionSettingsMixin(BaseModel):
    transcription_provider: TranscriptionProvider = TranscriptionProvider.WHISPER_SERVER
    openai_api_key: SecretStr | None = None


def has_openai_key(settings: TranscriptionSettingsMixin) -> bool:
    return settings.openai_api_key is not None and bool(
        settings.openai_api_key.get_secret_value().strip()
    )
