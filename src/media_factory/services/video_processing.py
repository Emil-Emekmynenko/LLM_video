import subprocess
from collections.abc import Callable
from pathlib import Path

from media_factory.domain.analysis import ClipInterval

Runner = Callable[..., subprocess.CompletedProcess[str]]


class VideoProcessingError(RuntimeError):
    code = "video_processing_failed"


class FFmpegProxyGenerator:
    def __init__(self, ffmpeg_bin: str = "ffmpeg", runner: Runner = subprocess.run) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.runner = runner

    def generate(self, source: Path, destination: Path) -> list[str]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.ffmpeg_bin,
            "-v",
            "error",
            "-n",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            "scale=1280:-2:force_original_aspect_ratio=decrease,fps=10",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            str(destination),
        ]
        self._run(command, destination, "proxy generation failed")
        return command

    def _run(self, command: list[str], destination: Path, fallback_message: str) -> None:
        try:
            result = self.runner(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            destination.unlink(missing_ok=True)
            raise VideoProcessingError("ffmpeg is not installed or not on PATH") from exc
        if result.returncode != 0:
            destination.unlink(missing_ok=True)
            raise VideoProcessingError(result.stderr.strip() or fallback_message)


class FFmpegClipExtractor:
    def __init__(self, ffmpeg_bin: str = "ffmpeg", runner: Runner = subprocess.run) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.runner = runner

    def extract(self, source: Path, destination: Path, interval: ClipInterval) -> list[str]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.ffmpeg_bin,
            "-v",
            "error",
            "-n",
            "-ss",
            f"{interval.start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{interval.duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            str(destination),
        ]
        try:
            result = self.runner(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            destination.unlink(missing_ok=True)
            raise VideoProcessingError("ffmpeg is not installed or not on PATH") from exc
        if result.returncode != 0:
            destination.unlink(missing_ok=True)
            raise VideoProcessingError(result.stderr.strip() or "clip extraction failed")
        return command


def split_scene_intervals(
    scenes: list[ClipInterval],
    *,
    max_duration: float,
    overlap: float,
) -> list[ClipInterval]:
    if max_duration <= 0:
        raise ValueError("max_duration must be positive")
    if overlap < 0 or overlap >= max_duration:
        raise ValueError("overlap must be non-negative and lower than max_duration")

    clips: list[ClipInterval] = []
    step = max_duration - overlap
    for scene in scenes:
        start = scene.start
        while start < scene.end:
            end = min(start + max_duration, scene.end)
            clips.append(ClipInterval(start=start, end=end))
            if end >= scene.end:
                break
            start += step
    return clips
