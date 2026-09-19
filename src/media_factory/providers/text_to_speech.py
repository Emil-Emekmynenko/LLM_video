import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_factory.domain.narration import NarrationScript


@dataclass(frozen=True)
class SynthesizedAudio:
    path: Path
    duration: float
    mime_type: str
    synthetic: bool


class TextToSpeechProvider(Protocol):
    name: str
    version: str
    parameters: dict[str, str | int | float | bool | None]

    def synthesize(
        self,
        script: NarrationScript,
        destination: Path,
    ) -> SynthesizedAudio: ...


class FakeTextToSpeechProvider:
    """Development-only provider producing silence, never simulated speech."""

    name = "fake-tts-silence"
    version = "1.0"
    parameters: dict[str, str | int | float | bool | None] = {
        "sample_rate": 16000,
        "sample_width": 2,
        "channels": 1,
        "contains_speech": False,
    }

    def synthesize(
        self,
        script: NarrationScript,
        destination: Path,
    ) -> SynthesizedAudio:
        word_count = max(len(script.text.split()), 1)
        duration = max(word_count / script.target_wpm * 60, 0.25)
        sample_rate = 16000
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with wave.open(str(destination), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(sample_rate)
                audio.writeframes(b"\x00\x00" * round(duration * sample_rate))
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return SynthesizedAudio(
            path=destination,
            duration=duration,
            mime_type="audio/wav",
            synthetic=True,
        )
