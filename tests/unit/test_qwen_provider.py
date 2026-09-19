import json
from pathlib import Path

import httpx
import pytest

from media_factory.domain.analysis import ClipInterval
from media_factory.providers.video_understanding import (
    InvalidVLMResponseError,
    QwenOpenAICompatibleProvider,
)


def _provider(client: httpx.Client, *, max_retries: int = 1) -> QwenOpenAICompatibleProvider:
    return QwenOpenAICompatibleProvider(
        base_url="http://qwen.local/v1",
        api_key="test-key",
        model="Qwen/Qwen3-VL-8B-Instruct",
        model_revision="revision-123",
        timeout_seconds=30,
        max_retries=max_retries,
        temperature=0.1,
        max_tokens=512,
        client=client,
    )


def test_qwen_provider_sends_video_schema_and_retries_invalid_json(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    valid_result = {
        "summary": "A person lifts a screwdriver.",
        "participants": ["person_1"],
        "objects": ["screwdriver"],
        "events": [
            {
                "relative_start": 1.0,
                "relative_end": 2.0,
                "actor": "person_1",
                "action": "picks_up_object",
                "objects": ["screwdriver"],
                "evidence": "A hand closes around and lifts the screwdriver.",
                "confidence": 0.92,
            }
        ],
        "suggested_chapters": [],
        "uncertainty": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        content = "not-json" if len(calls) == 1 else json.dumps(valid_result)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": content}}]},
        )

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video-bytes")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _provider(client).analyze_clip(clip, ClipInterval(start=10, end=15))

    assert result.events[0].action == "picks_up_object"
    assert len(calls) == 2
    payload = json.loads(calls[0].content)
    video_url = payload["messages"][1]["content"][0]["video_url"]["url"]
    assert video_url.startswith("data:video/mp4;base64,")
    assert payload["response_format"]["type"] == "json_schema"
    assert calls[0].headers["Authorization"] == "Bearer test-key"


def test_qwen_provider_fails_after_bounded_retries(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "{}"}}]},
        )

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video-bytes")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(InvalidVLMResponseError):
            _provider(client, max_retries=0).analyze_clip(
                clip,
                ClipInterval(start=0, end=5),
            )
