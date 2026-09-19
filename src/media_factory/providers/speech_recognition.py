from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Protocol

from media_factory.domain.models import TranscriptSegment, TranscriptWord
from media_factory.domain.transcription import ASRResult


class SpeechRecognitionProvider(Protocol):
    name: str
    version: str
    model_name: str
    model_revision: str
    parameters: dict[str, Any]

    def transcribe(self, master: Path, *, media_duration: float) -> ASRResult: ...


class FasterWhisperProvider:
    name = "faster-whisper"

    def __init__(
        self,
        *,
        model_name: str,
        model_revision: str,
        device: str,
        compute_type: str,
        language: str | None,
        beam_size: int,
        vad_filter: bool,
    ) -> None:
        self.model_name = model_name
        self.model_revision = model_revision
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.parameters: dict[str, Any] = {
            "device": device,
            "compute_type": compute_type,
            "language": language,
            "beam_size": beam_size,
            "vad_filter": vad_filter,
            "word_timestamps": True,
        }
        try:
            self.version = version("faster-whisper")
        except PackageNotFoundError:
            self.version = "not-installed"
        self._model: Any = None

    def transcribe(self, master: Path, *, media_duration: float) -> ASRResult:
        del media_duration
        model = self._load_model()
        raw_segments, info = model.transcribe(
            str(master),
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
            word_timestamps=True,
        )
        segments: list[TranscriptSegment] = []
        full_text: list[str] = []
        for segment in raw_segments:
            text = str(segment.text).strip()
            full_text.append(text)
            words = [
                TranscriptWord(
                    word=str(word.word).strip(),
                    start=float(word.start),
                    end=float(word.end),
                )
                for word in (segment.words or [])
                if word.start is not None and word.end is not None
            ]
            segments.append(
                TranscriptSegment(
                    start=float(segment.start),
                    end=float(segment.end),
                    text=text,
                    words=words,
                )
            )
        return ASRResult(
            language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
            text=" ".join(part for part in full_text if part),
            segments=segments,
        )

    def _load_model(self) -> Any:
        if self._model is None:
            try:
                module = import_module("faster_whisper")
            except ModuleNotFoundError as exc:
                raise SpeechRecognitionUnavailable(
                    "faster-whisper is not installed; install the 'asr' extra"
                ) from exc
            self._model = module.WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model


class FakeSpeechRecognitionProvider:
    """Development-only deterministic provider; it does not inspect the audio."""

    name = "fake-asr"
    version = "1.0"
    model_name = "deterministic-development-fixture"
    model_revision = "1"
    parameters: dict[str, Any] = {"word_timestamps": True, "synthetic": True}

    def transcribe(self, master: Path, *, media_duration: float) -> ASRResult:
        if not master.is_file():
            raise FileNotFoundError(master)
        end = min(media_duration, 1.0)
        midpoint = end / 2
        return ASRResult(
            language="en",
            language_probability=1.0,
            text="development transcript",
            segments=[
                TranscriptSegment(
                    start=0,
                    end=end,
                    text="development transcript",
                    words=[
                        TranscriptWord(word="development", start=0, end=midpoint),
                        TranscriptWord(word="transcript", start=midpoint, end=end),
                    ],
                )
            ],
        )


class SpeechRecognitionUnavailable(RuntimeError):
    code = "asr_provider_unavailable"
