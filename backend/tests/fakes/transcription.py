from dataclasses import dataclass, field

from taskbot.domain.ports.transcription import Transcript
from taskbot.domain.voice_jobs import TranscriptionProvider
from tests.fakes.calls import Calls
from tests.fakes.persistence import inside_unit_of_work


@dataclass
class ScriptedTranscriber:
    script: list[Transcript | BaseException]
    calls: Calls = field(default_factory=Calls)
    provider: TranscriptionProvider = TranscriptionProvider.FAKE
    audio: list[bytes] = field(default_factory=list)

    async def transcribe(self, audio: bytes) -> Transcript:
        assert not inside_unit_of_work.get(), "Transcription inside a unit of work"
        self.calls.items.append("transcriber.transcribe")
        self.audio.append(audio)
        result = self.script.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result
