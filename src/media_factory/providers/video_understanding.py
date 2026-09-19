from pathlib import Path
from typing import Protocol

from media_factory.domain.analysis import ClipAnalysis, ClipInterval


class VideoUnderstandingProvider(Protocol):
    name: str
    version: str
    prompt_version: str

    def analyze_clip(self, clip_path: Path, interval: ClipInterval) -> ClipAnalysis: ...


class FakeVideoUnderstandingProvider:
    name = "fake-vlm"
    version = "1.0"
    prompt_version = "video-analysis-v1"

    def analyze_clip(self, clip_path: Path, interval: ClipInterval) -> ClipAnalysis:
        return ClipAnalysis(
            summary=(
                f"Synthetic analysis for {clip_path.name} "
                f"from {interval.start:.3f}s to {interval.end:.3f}s"
            ),
            uncertainty="Fake provider: no visual inference was performed.",
        )

