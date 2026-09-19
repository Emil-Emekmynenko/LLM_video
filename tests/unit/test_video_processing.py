import subprocess
from pathlib import Path

import pytest

from media_factory.domain.analysis import ClipInterval
from media_factory.services.video_processing import (
    FFmpegClipExtractor,
    FFmpegProxyGenerator,
    VideoProcessingError,
    split_scene_intervals,
)


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        self.commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")


class FailingRunner:
    def __init__(self, partial_path: Path) -> None:
        self.partial_path = partial_path

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        self.partial_path.touch()
        return subprocess.CompletedProcess(command, 1, "", "broken media")


def test_long_scene_is_split_with_overlap() -> None:
    clips = split_scene_intervals(
        [ClipInterval(start=0, end=32)],
        max_duration=15,
        overlap=2,
    )

    assert [(clip.start, clip.end) for clip in clips] == [
        (0.0, 15.0),
        (13.0, 28.0),
        (26.0, 32.0),
    ]


def test_invalid_overlap_is_rejected() -> None:
    with pytest.raises(ValueError, match="overlap"):
        split_scene_intervals(
            [ClipInterval(start=0, end=10)],
            max_duration=10,
            overlap=10,
        )


def test_proxy_command_only_writes_derived_destination(tmp_path: Path) -> None:
    runner = RecordingRunner()
    source = tmp_path / "master.mp4"
    destination = tmp_path / "proxies" / "proxy.mp4"
    generator = FFmpegProxyGenerator(runner=runner)

    command = generator.generate(source, destination)

    assert "-n" in command
    assert "-an" in command
    assert str(source) in command
    assert command[-1] == str(destination)
    assert "-c:v" in command
    assert source != destination


def test_clip_command_uses_absolute_interval(tmp_path: Path) -> None:
    runner = RecordingRunner()
    extractor = FFmpegClipExtractor(runner=runner)

    command = extractor.extract(
        tmp_path / "proxy.mp4",
        tmp_path / "clip.mp4",
        ClipInterval(start=13, end=28),
    )

    assert command[command.index("-ss") + 1] == "13.000"
    assert command[command.index("-t") + 1] == "15.000"


def test_failed_proxy_removes_partial_derived_file(tmp_path: Path) -> None:
    destination = tmp_path / "proxy.mp4"
    generator = FFmpegProxyGenerator(runner=FailingRunner(destination))

    with pytest.raises(VideoProcessingError, match="broken media"):
        generator.generate(tmp_path / "master.mp4", destination)

    assert destination.exists() is False
