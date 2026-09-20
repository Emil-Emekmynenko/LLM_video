import shutil
import subprocess
from pathlib import Path

import pytest

from media_factory.domain.analysis import ClipInterval
from media_factory.domain.narration import AudioPolicy
from media_factory.services.master_processing import (
    FFmpegDecodeValidator,
    FFmpegMasterAssembler,
)
from media_factory.services.media_inspector import FFprobeMediaInspector
from media_factory.services.video_processing import (
    FFmpegClipExtractor,
    FFmpegProxyGenerator,
)


@pytest.mark.ffmpeg
def test_real_ffmpeg_media_path_preserves_master_video_stream(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg is not installed")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    inspector = FFprobeMediaInspector()
    source_inspection = inspector.inspect(source)
    assert source_inspection.duration == pytest.approx(2, abs=0.1)
    assert len(source_inspection.video_streams) == 1
    assert len(source_inspection.audio_streams) == 1

    proxy = tmp_path / "proxy.mp4"
    FFmpegProxyGenerator().generate(source, proxy)
    proxy_inspection = inspector.inspect(proxy)
    assert proxy_inspection.duration == pytest.approx(source_inspection.duration, abs=0.1)

    clip = tmp_path / "clip.mp4"
    FFmpegClipExtractor().extract(source, clip, ClipInterval(start=0.25, end=1.25))
    assert inspector.inspect(clip).duration == pytest.approx(1, abs=0.15)

    master = tmp_path / "master.mp4"
    command = FFmpegMasterAssembler().build(
        source=source,
        destination=master,
        policy=AudioPolicy.PRESERVE,
        source_duration=source_inspection.duration,
        audio_track=None,
    )
    master_inspection = inspector.inspect(master)
    assert command[command.index("-c:v") + 1] == "copy"
    assert master_inspection.video_streams[0].codec_name == (
        source_inspection.video_streams[0].codec_name
    )
    assert master_inspection.video_streams[0].width == 320
    assert master_inspection.video_streams[0].height == 180
    FFmpegDecodeValidator().validate(master)
