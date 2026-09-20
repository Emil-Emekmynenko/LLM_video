import subprocess
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_factory.domain.narration import NarrationScript

Runner = Callable[..., subprocess.CompletedProcess[str]]


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


class PiperTextToSpeechProvider:
    name = "piper"

    def __init__(
        self,
        *,
        model_path: Path,
        model_revision: str,
        piper_bin: str = "piper",
        speaker: int | None = None,
        length_scale: float = 1.0,
        runner: Runner = subprocess.run,
    ) -> None:
        self.model_path = model_path
        self.version = model_revision
        self.piper_bin = piper_bin
        self.speaker = speaker
        self.length_scale = length_scale
        self.runner = runner
        self.parameters: dict[str, str | int | float | bool | None] = {
            "model": model_path.name,
            "model_revision": model_revision,
            "speaker": speaker,
            "length_scale": length_scale,
        }

    def synthesize(
        self,
        script: NarrationScript,
        destination: Path,
    ) -> SynthesizedAudio:
        if not self.model_path.is_file() or self.model_path.is_symlink():
            raise TextToSpeechUnavailable("Piper model is missing or unsafe")
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.piper_bin,
            "--model",
            str(self.model_path),
            "--output_file",
            str(destination),
            "--length_scale",
            str(self.length_scale),
        ]
        if self.speaker is not None:
            command.extend(["--speaker", str(self.speaker)])
        try:
            result = self.runner(
                command,
                input=script.text,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise TextToSpeechUnavailable("Piper is not installed or not on PATH") from exc
        if result.returncode != 0:
            destination.unlink(missing_ok=True)
            raise TextToSpeechUnavailable(result.stderr.strip() or "Piper synthesis failed")
        try:
            with wave.open(str(destination), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
        except (OSError, wave.Error, ZeroDivisionError) as exc:
            destination.unlink(missing_ok=True)
            raise TextToSpeechUnavailable("Piper returned invalid WAV audio") from exc
        if duration <= 0:
            destination.unlink(missing_ok=True)
            raise TextToSpeechUnavailable("Piper returned empty audio")
        return SynthesizedAudio(
            path=destination,
            duration=duration,
            mime_type="audio/wav",
            synthetic=True,
        )


class TextToSpeechUnavailable(RuntimeError):
    code = "tts_provider_unavailable"
