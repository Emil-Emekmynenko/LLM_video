import base64
import subprocess
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

import httpx

from media_factory.domain.analysis import (
    ClipAnalysis,
    ClipInterval,
    DetectedEvent,
    SuggestedChapter,
)

PROMPT_VERSION = "video-analysis-v6"


class VideoUnderstandingProvider(Protocol):
    name: str
    version: str
    prompt_version: str
    inference_parameters: dict[str, str | int | float | bool | None]

    def analyze_clip(self, clip_path: Path, interval: ClipInterval) -> ClipAnalysis: ...


class VideoUnderstandingError(RuntimeError):
    code = "vlm_inference_failed"


class InvalidVLMResponseError(VideoUnderstandingError):
    code = "invalid_vlm_response"


class QwenOpenAICompatibleProvider:
    """Qwen3-VL adapter for a local OpenAI-compatible inference server."""

    name = "qwen3-vl-openai-compatible"
    prompt_version = PROMPT_VERSION

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        model_revision: str,
        timeout_seconds: float,
        max_retries: int,
        temperature: float,
        max_tokens: int,
        media_mode: str = "video",
        frame_count: int = 6,
        frame_max_width: int = 960,
        ffmpeg_bin: str = "ffmpeg",
        frame_sampler: Callable[[Path, float, int, int], list[bytes]] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self.api_key = api_key
        self.model = model
        self.version = model_revision
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.temperature = temperature
        self.max_tokens = max_tokens
        if media_mode not in {"video", "frames"}:
            raise ValueError("media_mode must be 'video' or 'frames'")
        self.media_mode = media_mode
        self.frame_count = frame_count
        self.frame_max_width = frame_max_width
        self.ffmpeg_bin = ffmpeg_bin
        self.frame_sampler = frame_sampler or self._sample_frames
        self.client = client
        self.inference_parameters: dict[str, str | int | float | bool | None] = {
            "model": model,
            "model_revision": model_revision,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "max_retries": max_retries,
            "media_mode": media_mode,
            "frame_count": frame_count if media_mode == "frames" else None,
            "frame_max_width": frame_max_width if media_mode == "frames" else None,
        }

    def analyze_clip(self, clip_path: Path, interval: ClipInterval) -> ClipAnalysis:
        payload = self._payload(clip_path, interval)
        last_error: Exception | None = None

        for _ in range(self.max_retries + 1):
            try:
                response = self._post(payload)
                response.raise_for_status()
                content = self._extract_content(response.json())
                result = ClipAnalysis.model_validate_json(self._strip_code_fence(content))
                return self._normalize_timestamps(result, interval)
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                last_error = exc

        attempts = self.max_retries + 1
        if isinstance(last_error, httpx.HTTPError):
            raise VideoUnderstandingError(
                f"Qwen request failed after {attempts} attempts"
            ) from last_error
        raise InvalidVLMResponseError(
            f"Qwen response remained invalid after {attempts} attempts"
        ) from last_error

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.client is not None:
            return self.client.post(
                self.endpoint,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        with httpx.Client() as client:
            return client.post(
                self.endpoint,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )

    def _payload(self, clip_path: Path, interval: ClipInterval) -> dict[str, Any]:
        media: list[dict[str, Any]]
        if self.media_mode == "frames":
            frames = self.frame_sampler(
                clip_path,
                interval.duration,
                self.frame_count,
                self.frame_max_width,
            )
            if not frames:
                raise VideoUnderstandingError("FFmpeg did not produce analysis frames")
            media = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64," + base64.b64encode(frame).decode("ascii")
                    },
                }
                for frame in frames
            ]
            media_description = (
                f"These {len(frames)} frames are ordered uniformly through a "
                f"{interval.duration:.3f}-second clip."
            )
        else:
            video_data = base64.b64encode(clip_path.read_bytes()).decode("ascii")
            media = [
                {
                    "type": "video_url",
                    "video_url": {"url": f"data:video/mp4;base64,{video_data}"},
                }
            ]
            media_description = f"This video clip is {interval.duration:.3f} seconds long."
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Describe only directly observable visual facts. Do not identify "
                        "people, infer intentions, or treat speech as a completed action. "
                        "Use unknown_action when an action cannot be established. Return only "
                        "JSON matching the supplied schema. All event and chapter timestamps "
                        "must be relative to the start of this clip. Write summaries, actor and "
                        "action labels, evidence, and chapter titles in Russian. Populate events "
                        "for every directly observable action or state change, and populate "
                        "participants and objects mentioned in the summary. Do not leave events "
                        "empty when the summary describes an action. Be concise: use one sentence "
                        "for the summary, no more than four events, and one short sentence of "
                        "evidence per event."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        *media,
                        {
                            "type": "text",
                            "text": (
                                "Analyze the observable actions and changes over time. "
                                + media_description
                            ),
                        },
                    ],
                },
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "clip_analysis",
                    "strict": True,
                    "schema": ClipAnalysis.model_json_schema(),
                },
            },
        }

    def _sample_frames(
        self,
        clip_path: Path,
        duration: float,
        frame_count: int,
        max_width: int,
    ) -> list[bytes]:
        frames: list[bytes] = []
        with TemporaryDirectory(prefix="media-factory-frames-") as temp_dir:
            temp_path = Path(temp_dir)
            for index in range(frame_count):
                timestamp = duration * (index + 0.5) / frame_count
                output = temp_path / f"frame-{index:03d}.jpg"
                command = [
                    self.ffmpeg_bin,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{timestamp:.6f}",
                    "-i",
                    str(clip_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale='min({max_width},iw)':-2",
                    "-q:v",
                    "3",
                    "-y",
                    str(output),
                ]
                try:
                    subprocess.run(command, check=True, capture_output=True)
                except (OSError, subprocess.CalledProcessError) as exc:
                    raise VideoUnderstandingError(
                        f"Could not sample frame at {timestamp:.3f}s"
                    ) from exc
                if output.is_file():
                    frames.append(output.read_bytes())
        return frames

    @staticmethod
    def _normalize_timestamps(
        result: ClipAnalysis,
        interval: ClipInterval,
    ) -> ClipAnalysis:
        """Correct common model timestamp conventions without accepting wild values."""

        tolerance = 0.5

        def relative(value: float) -> float:
            if value <= interval.duration + tolerance:
                return min(value, interval.duration)
            if interval.start - tolerance <= value <= interval.end + tolerance:
                return min(max(value - interval.start, 0.0), interval.duration)
            return value

        def event_times(event: DetectedEvent) -> tuple[float, float]:
            absolute_bounds = (
                interval.start - tolerance <= event.relative_start <= interval.end + tolerance
                and interval.start - tolerance <= event.relative_end <= interval.end + tolerance
            )
            absolute_signal = (
                event.relative_start > interval.duration + tolerance
                or event.relative_end > interval.duration + tolerance
            )
            if absolute_bounds and absolute_signal:
                return (
                    min(max(event.relative_start - interval.start, 0.0), interval.duration),
                    min(max(event.relative_end - interval.start, 0.0), interval.duration),
                )
            return relative(event.relative_start), relative(event.relative_end)

        events: list[DetectedEvent] = []
        for event in result.events:
            relative_start, relative_end = event_times(event)
            events.append(
                DetectedEvent(
                    **event.model_dump(exclude={"relative_start", "relative_end"}),
                    relative_start=relative_start,
                    relative_end=relative_end,
                )
            )
        chapters = [
            SuggestedChapter(
                **chapter.model_dump(exclude={"relative_start"}),
                relative_start=relative(chapter.relative_start),
            )
            for chapter in result.suggested_chapters
        ]
        return result.model_copy(update={"events": events, "suggested_chapters": chapters})

    @staticmethod
    def _extract_content(body: Any) -> str:
        if not isinstance(body, dict):
            raise TypeError("VLM response body must be an object")
        content = body["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("VLM response content must be a string")
        return content

    @staticmethod
    def _strip_code_fence(content: str) -> str:
        stripped = content.strip()
        if not stripped.startswith("```"):
            return stripped
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1])
        return stripped


class FakeVideoUnderstandingProvider:
    name = "fake-vlm"
    version = "1.0"
    prompt_version = PROMPT_VERSION
    inference_parameters: dict[str, str | int | float | bool | None] = {"synthetic": True}

    def analyze_clip(self, clip_path: Path, interval: ClipInterval) -> ClipAnalysis:
        return ClipAnalysis(
            summary=(
                f"Synthetic analysis for {clip_path.name} "
                f"from {interval.start:.3f}s to {interval.end:.3f}s"
            ),
            uncertainty="Fake provider: no visual inference was performed.",
        )
