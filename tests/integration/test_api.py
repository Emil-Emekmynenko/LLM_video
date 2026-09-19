from pathlib import Path

from fastapi.testclient import TestClient

from media_factory.api.app import app, get_ingest_service, get_inspector
from media_factory.domain.models import MediaInspection, StreamInfo
from media_factory.services.asset_ingest import AssetIngestService, InMemoryAssetIndex


class FakeInspector:
    def is_available(self) -> bool:
        return True

    def inspect(self, path: Path) -> MediaInspection:
        return MediaInspection(
            duration=1.0,
            size_bytes=path.stat().st_size,
            format_name="mov,mp4",
            streams=[StreamInfo(index=0, codec_type="video", codec_name="h264")],
            raw={},
        )


def test_health_live() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_and_detect_duplicate(tmp_path: Path) -> None:
    inspector = FakeInspector()
    index = InMemoryAssetIndex()
    service = AssetIngestService(
        upload_dir=tmp_path,
        max_upload_bytes=1024,
        inspector=inspector,  # type: ignore[arg-type]
        index=index,
    )
    app.dependency_overrides[get_ingest_service] = lambda: service
    app.dependency_overrides[get_inspector] = lambda: inspector

    try:
        client = TestClient(app)
        first = client.post(
            "/api/v1/assets/uploads",
            files={"file": ("first.mp4", b"same-video", "video/mp4")},
        )
        second = client.post(
            "/api/v1/assets/uploads",
            files={"file": ("renamed.mp4", b"same-video", "video/mp4")},
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["duplicate_of"] == first.json()["id"]


def test_transcript_validation_endpoint() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/v1/transcripts/validate",
        json={
            "asset_id": "asset-1",
            "master_sha256": "a" * 64,
            "media_duration": 10,
            "segments": [{"start": 0, "end": 1, "text": "Hello", "words": []}],
        },
    )

    assert response.status_code == 200
    assert response.json()[0]["code"] == "no_word_timings"

