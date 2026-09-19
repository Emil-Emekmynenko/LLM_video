import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from media_factory.domain.models import MediaInspection, StreamInfo

Runner = Callable[..., subprocess.CompletedProcess[str]]


class MediaInspectionError(RuntimeError):
    code = "media_inspection_failed"


class MissingDependencyError(MediaInspectionError):
    code = "ffprobe_missing"


class InvalidMediaError(MediaInspectionError):
    code = "invalid_media"


class FFprobeMediaInspector:
    def __init__(self, ffprobe_bin: str = "ffprobe", runner: Runner = subprocess.run) -> None:
        self.ffprobe_bin = ffprobe_bin
        self.runner = runner

    def is_available(self) -> bool:
        return shutil.which(self.ffprobe_bin) is not None

    def inspect(self, path: Path) -> MediaInspection:
        command = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ]
        try:
            result = self.runner(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise MissingDependencyError("ffprobe is not installed or not on PATH") from exc

        if result.returncode != 0:
            detail = result.stderr.strip() or "ffprobe rejected the media file"
            raise InvalidMediaError(detail)

        try:
            payload: dict[str, Any] = json.loads(result.stdout)
            format_data = payload["format"]
            duration = float(format_data["duration"])
            size_bytes = int(format_data["size"])
            format_name = str(format_data["format_name"])
            raw_streams: list[dict[str, Any]] = payload.get("streams", [])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidMediaError("ffprobe returned an unexpected response") from exc

        streams = [StreamInfo.model_validate(stream) for stream in raw_streams]
        if not any(stream.codec_type == "video" for stream in streams):
            raise InvalidMediaError("media contains no video stream")

        return MediaInspection(
            duration=duration,
            size_bytes=size_bytes,
            format_name=format_name,
            streams=streams,
            raw=payload,
        )
