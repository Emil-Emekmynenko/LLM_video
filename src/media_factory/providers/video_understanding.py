import base64
from pathlib import Path
from typing import Any, Protocol

import httpx

from media_factory.domain.analysis import ClipAnalysis, ClipInterval

PROMPT_VERSION = "video-analysis-v2"


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
        self.client = client
        self.inference_parameters: dict[str, str | int | float | bool | None] = {
            "model": model,
            "model_revision": model_revision,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "max_retries": max_retries,
        }

    def analyze_clip(self, clip_path: Path, interval: ClipInterval) -> ClipAnalysis:
        video_data = base64.b64encode(clip_path.read_bytes()).decode("ascii")
        payload = self._payload(video_data, interval)
        last_error: Exception | None = None

        for _ in range(self.max_retries + 1):
            try:
                response = self._post(payload)
                response.raise_for_status()
                content = self._extract_content(response.json())
                return ClipAnalysis.model_validate_json(self._strip_code_fence(content))
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

    def _payload(self, video_data: str, interval: ClipInterval) -> dict[str, Any]:
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
                        "must be relative to the start of this clip."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "video_url",
                            "video_url": {"url": f"data:video/mp4;base64,{video_data}"},
                        },
                        {
                            "type": "text",
                            "text": (
                                "Analyze this video clip. Its duration is "
                                f"{interval.duration:.3f} seconds."
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
    inference_parameters: dict[str, str | int | float | bool | None] = {
        "synthetic": True
    }

    def analyze_clip(self, clip_path: Path, interval: ClipInterval) -> ClipAnalysis:
        return ClipAnalysis(
            summary=(
                f"Synthetic analysis for {clip_path.name} "
                f"from {interval.start:.3f}s to {interval.end:.3f}s"
            ),
            uncertainty="Fake provider: no visual inference was performed.",
        )
