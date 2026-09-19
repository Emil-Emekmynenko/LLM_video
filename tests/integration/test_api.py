from pathlib import Path

from fastapi.testclient import TestClient

from media_factory.api.app import (
    app,
    get_database,
    get_ingest_service,
    get_inspector,
    get_job_queue,
)
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


class FakeDatabase:
    def __init__(self, available: bool) -> None:
        self.available = available

    def is_available(self) -> bool:
        return self.available


class FakeQueue:
    def is_available(self) -> bool:
        return True


def test_health_live() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_local_mode_exposes_admin_principal() -> None:
    client = TestClient(app)

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json() == {"actor": "local-admin", "role": "admin"}


def test_current_customer_schema_is_exposed() -> None:
    client = TestClient(app)

    response = client.get("/api/v1/customer-schemas")

    assert response.status_code == 200
    assert response.json()[0]["customer"] == "internal"
    assert response.json()[0]["version"] == "1.0"


def test_operator_console_and_static_assets_are_served() -> None:
    client = TestClient(app)

    page = client.get("/")
    stylesheet = client.get("/ui/app.css")
    script = client.get("/ui/app.js")

    assert page.status_code == 200
    assert "Media Factory" in page.text
    assert 'id="review-dialog"' in page.text
    assert stylesheet.status_code == 200
    assert "--accent" in stylesheet.text
    assert script.status_code == 200
    assert "loadPackages" in script.text
    assert "renderEventReview" in script.text
    assert "renderNarrationReview" in script.text
    assert "renderQa" in script.text
    assert "renderRelease" in script.text


def test_readiness_reports_each_dependency() -> None:
    inspector = FakeInspector()
    app.dependency_overrides[get_inspector] = lambda: inspector
    app.dependency_overrides[get_database] = lambda: FakeDatabase(available=False)
    app.dependency_overrides[get_job_queue] = lambda: FakeQueue()

    try:
        client = TestClient(app)
        response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": False, "ffprobe": True, "redis": True},
    }


def test_upload_and_detect_duplicate(tmp_path: Path) -> None:
    inspector = FakeInspector()
    index = InMemoryAssetIndex()
    service = AssetIngestService(
        upload_dir=tmp_path,
        max_upload_bytes=1024,
        inspector=inspector,  # type: ignore[arg-type]
        repository=index,
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
