from pydantic import BaseModel, HttpUrl, SecretStr

from taskbot.domain.voice_jobs import TranscriptionProvider


class TranscriptionSettingsMixin(BaseModel):
    transcription_provider: TranscriptionProvider = TranscriptionProvider.WHISPER_SERVER
    openai_api_key: SecretStr | None = None


class TranscriberSettingsMixin(TranscriptionSettingsMixin):
    openai_transcription_model: str = "gpt-transcribe"
    whisper_server_url: HttpUrl = HttpUrl("http://127.0.0.1:8178")


def has_openai_key(settings: TranscriptionSettingsMixin) -> bool:
    return settings.openai_api_key is not None and bool(
        settings.openai_api_key.get_secret_value().strip()
    )


def openai_transcription_unavailable(settings: TranscriptionSettingsMixin) -> bool:
    return settings.transcription_provider == TranscriptionProvider.OPENAI and not has_openai_key(
        settings
    )
