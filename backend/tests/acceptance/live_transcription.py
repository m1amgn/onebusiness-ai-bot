"""Opt-in provider probe for synthetic audio, including the Russian timing samples."""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from taskbot.domain.errors import (
    TranscriptionError,
    TransientTranscriptionError,
)
from taskbot.domain.limits import (
    MAX_VOICE_BYTES,
)
from taskbot.domain.voice_jobs import TranscriptionProvider
from taskbot.entrypoints.settings import load_settings
from taskbot.infrastructure.settings.base import BaseAppSettings
from taskbot.infrastructure.settings.transcription import (
    TranscriberSettingsMixin,
    has_openai_key,
)
from taskbot.infrastructure.transcription.factory import open_transcriber

# Direct script execution uses the same synthetic fixture as the worker tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.s5 import FIXTURE


class LiveTranscriptionSettings(BaseAppSettings, TranscriberSettingsMixin):
    pass


@dataclass(frozen=True, slots=True)
class Measurement:
    provider: str
    model: str | None
    elapsed_seconds: float
    text: str


def read_audio(path: Path) -> bytes:
    if path.stat().st_size > MAX_VOICE_BYTES:
        raise ValueError("Audio exceeds the 10 MB voice limit")
    return path.read_bytes()


async def measure(settings: LiveTranscriptionSettings, audio: bytes) -> Measurement:
    async with open_transcriber(settings) as transcriber:
        started = time.perf_counter()
        transcript = await transcriber.transcribe(audio)
        elapsed = time.perf_counter() - started
    return Measurement(
        provider=transcript.provider.value,
        model=(
            settings.openai_transcription_model
            if transcript.provider == TranscriptionProvider.OPENAI
            else None
        ),
        elapsed_seconds=round(elapsed, 3),
        text=transcript.text,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One live transcription call. OpenAI is paid; use synthetic audio."
    )
    parser.add_argument("--provider", choices=("whisper_server", "openai"), required=True)
    parser.add_argument("--file", type=Path, default=FIXTURE)
    args = parser.parse_args()
    settings = load_settings(LiveTranscriptionSettings).model_copy(
        update={"transcription_provider": TranscriptionProvider(args.provider)}
    )
    if settings.transcription_provider == TranscriptionProvider.OPENAI and not has_openai_key(
        settings
    ):
        parser.error("Set OPENAI_API_KEY in this worktree's untracked .env")
    try:
        audio = read_audio(args.file)
    except ValueError as error:
        parser.error(str(error))
    except OSError as error:
        parser.error(f"Cannot read --file ({type(error).__name__})")
    try:
        result = asyncio.run(measure(settings, audio))
    except TranscriptionError as error:
        print(json.dumps({"provider": args.provider, "error": error.kind.value}), file=sys.stderr)
        raise SystemExit(1) from None
    except TransientTranscriptionError:
        print(json.dumps({"provider": args.provider, "error": "transient"}), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        print(json.dumps({"error": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(asdict(result), ensure_ascii=False))


if __name__ == "__main__":
    main()
