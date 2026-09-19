from pathlib import Path

from pytest import MonkeyPatch

from media_factory.config import get_settings
from media_factory.domain.job import JobKind, JobState
from media_factory.domain.models import MediaInspection, StoredAsset, StreamInfo
from media_factory.domain.package_state import PackageState
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.job_repository import SQLAlchemyJobRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.services.media_inspector import FFprobeMediaInspector
from media_factory.workers.tasks import execute_job


def test_worker_reinspects_asset_and_completes_job(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'worker.sqlite3'}"
    database = Database(database_url)
    database.create_schema()
    asset = StoredAsset(
        id="asset-worker",
        original_name="video.mp4",
        stored_path=tmp_path / "video.mp4",
        size_bytes=123,
        sha256="b" * 64,
    )
    assets = SQLAlchemyAssetRepository(database.session_factory)
    assets.save(asset)
    packages = SQLAlchemyPackageRepository(database.session_factory)
    package = packages.create(asset.id)
    package = packages.transition(
        package.id,
        target=PackageState.INSPECTING,
        expected_version=package.version,
    )
    jobs = SQLAlchemyJobRepository(database.session_factory)
    job, _ = jobs.create_or_get(
        package_id=package.id,
        kind=JobKind.INSPECT_ASSET,
        idempotency_key="worker-inspect-1",
        payload={},
    )
    inspection = MediaInspection(
        duration=3.0,
        size_bytes=123,
        format_name="mov,mp4",
        streams=[StreamInfo(index=0, codec_type="video", codec_name="h264")],
    )
    monkeypatch.setenv("MEDIA_FACTORY_DATABASE_URL", database_url)
    monkeypatch.setattr(FFprobeMediaInspector, "inspect", lambda self, path: inspection)
    get_settings.cache_clear()

    try:
        execute_job(job.id)
    finally:
        get_settings.cache_clear()

    completed = jobs.get(job.id)
    updated_asset = assets.get(asset.id)
    updated_package = packages.get(package.id)
    assert completed.state is JobState.SUCCEEDED
    assert completed.progress == 100
    assert updated_asset.inspection is not None
    assert updated_asset.inspection.duration == 3.0
    assert updated_package.state is PackageState.READY_FOR_ANALYSIS
