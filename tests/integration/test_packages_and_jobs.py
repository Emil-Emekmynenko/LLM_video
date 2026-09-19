from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_factory.api.app import app, get_database, get_job_service, get_package_service
from media_factory.domain.errors import IdempotencyConflictError, VersionConflictError
from media_factory.domain.job import JobKind
from media_factory.domain.models import StoredAsset
from media_factory.domain.package_state import InvalidPackageTransition, PackageState
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.audit_repository import SQLAlchemyAuditRepository
from media_factory.persistence.database import Database
from media_factory.persistence.job_repository import SQLAlchemyJobRepository
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.services.job_queue import InMemoryJobQueue
from media_factory.services.job_service import JobService
from media_factory.services.package_service import PackageService


def seeded_database(tmp_path: Path) -> tuple[Database, StoredAsset]:
    database = Database(f"sqlite:///{tmp_path / 'factory.sqlite3'}")
    database.create_schema()
    asset = StoredAsset(
        id="asset-1",
        original_name="video.mp4",
        stored_path=tmp_path / "video.mp4",
        size_bytes=100,
        sha256="a" * 64,
    )
    SQLAlchemyAssetRepository(database.session_factory).save(asset)
    return database, asset


def test_package_transition_uses_optimistic_locking(tmp_path: Path) -> None:
    database, asset = seeded_database(tmp_path)
    service = PackageService(SQLAlchemyPackageRepository(database.session_factory))
    package = service.create(asset.id)

    transitioned = service.transition(
        package.id,
        target=PackageState.INSPECTING,
        expected_version=1,
    )

    assert transitioned.state is PackageState.INSPECTING
    assert transitioned.version == 2
    audit = SQLAlchemyAuditRepository(database.session_factory).list_events(
        package_id=package.id
    )
    assert len(audit) == 1
    assert audit[0].action == "package.state_changed"
    assert audit[0].details == {"from": "uploaded", "to": "inspecting"}
    assert audit[0].actor == "service-worker"
    with pytest.raises(VersionConflictError):
        service.transition(
            package.id,
            target=PackageState.READY_FOR_ANALYSIS,
            expected_version=1,
        )


def test_package_transition_cannot_skip_pipeline_steps(tmp_path: Path) -> None:
    database, asset = seeded_database(tmp_path)
    service = PackageService(SQLAlchemyPackageRepository(database.session_factory))
    package = service.create(asset.id)

    with pytest.raises(InvalidPackageTransition):
        service.transition(
            package.id,
            target=PackageState.COMPLETE,
            expected_version=1,
        )


def test_job_creation_is_idempotent_and_enqueues_once(tmp_path: Path) -> None:
    database, asset = seeded_database(tmp_path)
    package = SQLAlchemyPackageRepository(database.session_factory).create(asset.id)
    repository = SQLAlchemyJobRepository(database.session_factory)
    queue = InMemoryJobQueue()
    service = JobService(repository, queue)

    first = service.create(
        package_id=package.id,
        kind=JobKind.INSPECT_ASSET,
        idempotency_key="inspect-asset-1",
        payload={},
    )
    second = service.create(
        package_id=package.id,
        kind=JobKind.INSPECT_ASSET,
        idempotency_key="inspect-asset-1",
        payload={},
    )

    assert first.id == second.id
    assert first.dispatched_at is not None
    assert queue.enqueued == [first.id]

    with pytest.raises(IdempotencyConflictError):
        service.create(
            package_id=package.id,
            kind=JobKind.INSPECT_ASSET,
            idempotency_key="inspect-asset-1",
            payload={"different": True},
        )


def test_package_and_job_api_flow(tmp_path: Path) -> None:
    database, asset = seeded_database(tmp_path)
    package_service = PackageService(SQLAlchemyPackageRepository(database.session_factory))
    queue = InMemoryJobQueue()
    job_service = JobService(SQLAlchemyJobRepository(database.session_factory), queue)
    app.dependency_overrides[get_package_service] = lambda: package_service
    app.dependency_overrides[get_job_service] = lambda: job_service
    app.dependency_overrides[get_database] = lambda: database

    try:
        client = TestClient(app)
        created = client.post("/api/v1/packages", json={"source_asset_id": asset.id})
        package_id = created.json()["id"]
        transitioned = client.post(
            f"/api/v1/packages/{package_id}/transitions",
            json={"target": "inspecting", "expected_version": 1},
        )
        job = client.post(
            f"/api/v1/packages/{package_id}/jobs",
            headers={"Idempotency-Key": "api-inspect-1"},
            json={"kind": "inspect_asset", "payload": {}},
        )
        fetched = client.get(f"/api/v1/jobs/{job.json()['id']}")
        packages = client.get("/api/v1/packages")
        asset_response = client.get(f"/api/v1/assets/{asset.id}")
        jobs = client.get(f"/api/v1/packages/{package_id}/jobs")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert transitioned.status_code == 200
    assert transitioned.json()["state"] == "inspecting"
    assert job.status_code == 202
    assert fetched.status_code == 200
    assert fetched.json()["id"] == job.json()["id"]
    assert [item["id"] for item in packages.json()] == [package_id]
    assert asset_response.json()["original_name"] == "video.mp4"
    assert [item["id"] for item in jobs.json()] == [job.json()["id"]]
