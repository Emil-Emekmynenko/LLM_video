from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import update

from media_factory.domain.job import JobKind
from media_factory.domain.models import StoredAsset
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.job_repository import SQLAlchemyJobRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.persistence.tables import JobRow
from media_factory.services.job_queue import InMemoryJobQueue
from media_factory.services.job_service import JobService
from media_factory.services.metrics_service import MetricsService


def test_metrics_report_queue_state_and_stuck_jobs(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'metrics.sqlite3'}")
    database.create_schema()
    asset = StoredAsset(
        id="metrics-asset",
        original_name="source.mp4",
        stored_path=tmp_path / "source.mp4",
        size_bytes=1,
        sha256="a" * 64,
    )
    SQLAlchemyAssetRepository(database.session_factory).save(asset)
    package = SQLAlchemyPackageRepository(database.session_factory).create(asset.id)
    queue = InMemoryJobQueue()
    job = JobService(SQLAlchemyJobRepository(database.session_factory), queue).create(
        package_id=package.id,
        kind=JobKind.INSPECT_ASSET,
        idempotency_key="metrics-job",
        payload={},
    )
    stale = datetime.now(UTC) - timedelta(hours=2)
    with database.session_factory.begin() as session:
        session.execute(update(JobRow).where(JobRow.id == job.id).values(updated_at=stale))

    service = MetricsService(database.session_factory, queue, stuck_after_seconds=60)
    summary = service.summary()

    assert summary.packages_by_state == {"uploaded": 1}
    assert summary.jobs_by_state == {"queued": 1}
    assert summary.queue_depth == 1
    assert [item.id for item in summary.stuck_jobs] == [job.id]
    prometheus = service.prometheus()
    assert 'media_factory_packages{state="uploaded"} 1' in prometheus
    assert "media_factory_stuck_jobs 1" in prometheus
