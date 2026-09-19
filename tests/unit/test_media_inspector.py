import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from media_factory.services.media_inspector import FFprobeMediaInspector, InvalidMediaError


def completed(payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )


def test_inspector_parses_ffprobe_output() -> None:
    payload = {
        "format": {"duration": "12.5", "size": "1024", "format_name": "mov,mp4"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30/1",
            },
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
        ],
    }

    inspector = FFprobeMediaInspector(runner=lambda *args, **kwargs: completed(payload))
    result = inspector.inspect(Path("example.mp4"))

    assert result.duration == 12.5
    assert result.size_bytes == 1024
    assert result.video_streams[0].width == 1920
    assert result.audio_streams[0].codec_name == "aac"


def test_inspector_rejects_audio_only_file() -> None:
    payload = {
        "format": {"duration": "2", "size": "50", "format_name": "wav"},
        "streams": [{"index": 0, "codec_type": "audio", "codec_name": "pcm_s16le"}],
    }
    inspector = FFprobeMediaInspector(runner=lambda *args, **kwargs: completed(payload))

    with pytest.raises(InvalidMediaError, match="no video stream"):
        inspector.inspect(Path("audio.wav"))

