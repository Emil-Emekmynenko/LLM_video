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


def _frames_provider(client: httpx.Client) -> QwenOpenAICompatibleProvider:
    return QwenOpenAICompatibleProvider(
        base_url="http://qwen.local/v1",
        api_key="",
        model="qwen3-vl:4b",
        model_revision="ollama",
        timeout_seconds=30,
        max_retries=0,
        temperature=0.1,
        max_tokens=512,
        media_mode="frames",
        frame_count=2,
        frame_sampler=lambda _path, _duration, _count, _width: [b"one", b"two"],
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
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["events"]["maxItems"] == 4
    assert schema["properties"]["suggested_chapters"]["maxItems"] == 4
    assert schema["properties"]["summary"]["maxLength"] == 300
    assert schema["properties"]["objects"]["items"]["maxLength"] == 100
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


def test_qwen_provider_can_send_ordered_sampled_frames(tmp_path: Path) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"summary": "A hand moves an object."})}}
                ]
            },
        )

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"unused")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _frames_provider(client).analyze_clip(clip, ClipInterval(start=0, end=10))

    assert result.summary == "A hand moves an object."
    payload = json.loads(captured[0].content)
    content = payload["messages"][1]["content"]
    assert [part["type"] for part in content] == ["image_url", "image_url", "text"]
    assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_qwen_provider_normalizes_absolute_clip_timestamps(tmp_path: Path) -> None:
    response = {
        "summary": "Человек складывает футболку.",
        "participants": ["человек"],
        "objects": ["футболка"],
        "events": [
            {
                "relative_start": 42.0,
                "relative_end": 45.2,
                "actor": "человек",
                "action": "складывает футболку",
                "objects": ["футболка"],
                "evidence": "Руки складывают ткань.",
                "confidence": 0.9,
            }
        ],
        "suggested_chapters": [
            {
                "relative_start": 42.0,
                "title": "Складывание",
                "evidence": "Футболку складывают.",
                "confidence": 0.9,
            }
        ],
        "uncertainty": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": json.dumps(response)}}]},
        )

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video-bytes")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _provider(client).analyze_clip(
            clip,
            ClipInterval(start=40.0, end=45.0),
        )

    assert result.events[0].relative_start == 2.0
    assert result.events[0].relative_end == 5.0
    assert result.suggested_chapters[0].relative_start == 2.0
