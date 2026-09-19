import subprocess
from pathlib import Path

import pytest

from media_factory.domain.narration import AudioPolicy
from media_factory.services.master_processing import (
    FFmpegDecodeValidator,
    FFmpegMasterAssembler,
    MasterProcessingError,
)


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (AudioPolicy.PRESERVE, ["-map", "0:a?", "-c:a", "copy"]),
        (AudioPolicy.REMOVE, ["-an"]),
        (AudioPolicy.REPLACE, ["-map", "1:a:0", "-c:a", "aac"]),
        (AudioPolicy.ADDITIONAL, ["-map", "0:a?", "-map", "1:a:0"]),
    ],
)
def test_master_commands_always_copy_video(
    tmp_path: Path,
    policy: AudioPolicy,
    expected: list[str],
) -> None:
    captured: list[str] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    assembler = FFmpegMasterAssembler(runner=runner)
    assembler.build(
        source=tmp_path / "source.mp4",
        destination=tmp_path / "master.mp4",
        policy=policy,
        source_duration=12.5,
        audio_track=(
            tmp_path / "voice.wav"
            if policy in {AudioPolicy.REPLACE, AudioPolicy.ADDITIONAL}
            else None
        ),
    )

    assert captured.count("-c:v") == 1
    assert captured[captured.index("-c:v") + 1] == "copy"
    for token in expected:
        assert token in captured


def test_failed_master_build_removes_partial_output(tmp_path: Path) -> None:
    destination = tmp_path / "partial.mp4"

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        destination.write_bytes(b"partial")
        return subprocess.CompletedProcess(command, 1, "", "mux failed")

    assembler = FFmpegMasterAssembler(runner=runner)
    with pytest.raises(MasterProcessingError, match="mux failed"):
        assembler.build(
            source=tmp_path / "source.mp4",
            destination=destination,
            policy=AudioPolicy.REMOVE,
            source_duration=2,
            audio_track=None,
        )

    assert not destination.exists()


def test_decode_validator_checks_all_streams() -> None:
    captured: list[str] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    command = FFmpegDecodeValidator(runner=runner).validate(Path("master.mp4"))

    assert command == captured
    assert ["-map", "0"] == command[command.index("-map") : command.index("-map") + 2]
    assert command[-3:] == ["-f", "null", "-"]
