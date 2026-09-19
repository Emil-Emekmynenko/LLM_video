from pathlib import Path
from typing import Protocol

from scenedetect import AdaptiveDetector, detect  # type: ignore[import-untyped]

from media_factory.domain.analysis import ClipInterval


class SceneDetector(Protocol):
    def detect(self, video_path: Path, duration: float) -> list[ClipInterval]: ...


class PySceneDetector:
    def detect(self, video_path: Path, duration: float) -> list[ClipInterval]:
        scenes = detect(str(video_path), AdaptiveDetector(), show_progress=False)
        intervals = [
            ClipInterval(start=start.get_seconds(), end=end.get_seconds())
            for start, end in scenes
            if end.get_seconds() > start.get_seconds()
        ]
        return intervals or [ClipInterval(start=0, end=duration)]


class WholeVideoSceneDetector:
    """Deterministic fallback used by tests and explicitly configured environments."""

    def detect(self, video_path: Path, duration: float) -> list[ClipInterval]:
        del video_path
        return [ClipInterval(start=0, end=duration)]
