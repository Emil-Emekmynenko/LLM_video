import subprocess
import wave
from datetime import UTC, datetime
from pathlib import Path

from media_factory.domain.narration import NarrationScript, NarrationScriptStatus
from media_factory.providers.text_to_speech import PiperTextToSpeechProvider


def test_piper_provider_writes_and_measures_real_wav(tmp_path: Path) -> None:
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"model-fixture")
    captured: dict[str, object] = {}

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["input"] = kwargs["input"]
        destination = Path(command[command.index("--output_file") + 1])
        with wave.open(str(destination), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 8000)
        return subprocess.CompletedProcess(command, 0, "", "")

    provider = PiperTextToSpeechProvider(
        model_path=model,
        model_revision="voice-r1",
        piper_bin="piper-test",
        speaker=2,
        length_scale=1.1,
        runner=runner,
    )
    script = NarrationScript(
        id="script-1",
        package_id="package-1",
        metadata_version_id="metadata-1",
        version=1,
        status=NarrationScriptStatus.APPROVED,
        text="Проверка локальной озвучки.",
        language="ru",
        style="neutral",
        target_wpm=130,
        created_by="operator",
        created_at=datetime.now(UTC),
    )

    artifact = provider.synthesize(script, tmp_path / "voice.wav")

    assert artifact.duration == 0.5
    assert artifact.mime_type == "audio/wav"
    assert artifact.synthetic is True
    assert captured["input"] == script.text
    command = captured["command"]
    assert isinstance(command, list)
    assert command[0] == "piper-test"
    assert command[command.index("--speaker") + 1] == "2"
