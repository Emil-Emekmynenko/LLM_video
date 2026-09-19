from pathlib import Path
from types import SimpleNamespace
from typing import Any

from media_factory.domain.models import Transcript
from media_factory.providers.speech_recognition import (
    FakeSpeechRecognitionProvider,
    FasterWhisperProvider,
)
from media_factory.services.transcript_validator import validate_transcript


def test_fake_asr_returns_valid_word_timestamps(tmp_path: Path) -> None:
    master = tmp_path / "master.mp4"
    master.write_bytes(b"master")
    result = FakeSpeechRecognitionProvider().transcribe(master, media_duration=5.0)
    transcript = Transcript(
        asset_id="asset-1",
        master_sha256="a" * 64,
        language=result.language,
        media_duration=5.0,
        segments=result.segments,
    )

    assert result.language_probability == 1.0
    assert validate_transcript(transcript) == []


class StubWhisperModel:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def transcribe(self, path: str, **kwargs: Any) -> tuple[list[Any], Any]:
        del path
        self.kwargs = kwargs
        segment = SimpleNamespace(
            start=0.0,
            end=1.0,
            text=" Hello world ",
            words=[
                SimpleNamespace(word=" Hello", start=0.0, end=0.4),
                SimpleNamespace(word=" world", start=0.5, end=1.0),
            ],
        )
        info = SimpleNamespace(language="en", language_probability=0.91)
        return [segment], info


def test_faster_whisper_adapter_requests_word_timestamps(tmp_path: Path) -> None:
    master = tmp_path / "master.mp4"
    master.write_bytes(b"master")
    provider = FasterWhisperProvider(
        model_name="small",
        model_revision="test-revision",
        device="cpu",
        compute_type="int8",
        language=None,
        beam_size=3,
        vad_filter=True,
    )
    model = StubWhisperModel()
    provider._model = model

    result = provider.transcribe(master, media_duration=5.0)

    assert model.kwargs["word_timestamps"] is True
    assert model.kwargs["beam_size"] == 3
    assert result.text == "Hello world"
    assert result.language == "en"
    assert result.segments[0].words[1].end == 1.0
