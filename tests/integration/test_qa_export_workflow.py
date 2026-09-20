import json
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from media_factory.domain.delivery import DeliveryCreateRequest, DeliveryState
from media_factory.domain.models import StoredAsset
from media_factory.domain.package_state import PackageState
from media_factory.domain.packaging import DeliveryMetadata, PackageBuild, PackageFile
from media_factory.domain.qa import ExportState, QADecision, QAReviewRequest
from media_factory.persistence.asset_repository import SQLAlchemyAssetRepository
from media_factory.persistence.database import Database
from media_factory.persistence.delivery_repository import SQLAlchemyDeliveryRepository
from media_factory.persistence.export_repository import SQLAlchemyExportRepository
from media_factory.persistence.package_build_repository import (
    SQLAlchemyPackageBuildRepository,
)
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.persistence.qa_repository import SQLAlchemyQARepository
from media_factory.persistence.tables import PackageRow
from media_factory.providers.object_storage import RemoteObject
from media_factory.services.checksum import sha256_file
from media_factory.services.delivery_service import DeliveryService
from media_factory.services.export_service import (
    ExportAlreadyExists,
    ExportWorkflowError,
    LocalExportService,
)
from media_factory.services.package_artifacts import write_deterministic_json
from media_factory.services.qa_service import QAArtifactMismatch, QAService


@dataclass
class MemoryDeliveryProvider:
    name: str = "memory"
    destination: str = "memory://test-bucket"

    def __post_init__(self) -> None:
        self.objects: dict[str, RemoteObject] = {}
        self.uploaded_keys: list[str] = []
        self.fail_suffix: str | None = None

    def upload_if_absent(
        self,
        source: Path,
        key: str,
        *,
        expected_size: int,
        expected_sha256: str,
    ) -> RemoteObject:
        if self.fail_suffix is not None and key.endswith(self.fail_suffix):
            raise RuntimeError("simulated interrupted upload")
        if key in self.objects:
            raise RuntimeError("remote object already exists")
        remote = RemoteObject(
            key=key,
            size_bytes=expected_size,
            sha256=expected_sha256,
            provider_checksum=expected_sha256,
        )
        self.objects[key] = remote
        self.uploaded_keys.append(key)
        return remote

    def inspect(self, key: str) -> RemoteObject:
        return self.objects[key]


def prepare_package_build(
    tmp_path: Path,
) -> tuple[
    Database,
    SQLAlchemyPackageRepository,
    SQLAlchemyPackageBuildRepository,
    SQLAlchemyQARepository,
    PackageBuild,
]:
    database = Database(f"sqlite:///{tmp_path / 'qa.sqlite3'}")
    database.create_schema()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    assets = SQLAlchemyAssetRepository(database.session_factory)
    assets.save(
        StoredAsset(
            id="qa-source",
            original_name="source.mp4",
            stored_path=source,
            size_bytes=source.stat().st_size,
            sha256=sha256_file(source),
        )
    )
    packages = SQLAlchemyPackageRepository(database.session_factory)
    package = packages.create("qa-source")
    with database.session_factory.begin() as session:
        row = session.get(PackageRow, package.id)
        assert row is not None
        row.state = PackageState.AWAITING_QA.value

    output_dir = tmp_path / "packages" / package.id / "v0001"
    output_dir.mkdir(parents=True)
    base_name = f"asset-{package.id}"
    files: list[PackageFile] = []
    for role, suffix, content, mime_type in (
        ("master", ".mp4", b"master", "video/mp4"),
        ("transcript", ".transcript.json", b"{}\n", "application/json"),
        ("metadata", ".metadata.json", b"{}\n", "application/json"),
    ):
        filename = f"{base_name}{suffix}"
        path = output_dir / filename
        path.write_bytes(content)
        files.append(
            PackageFile(
                role=role,
                filename=filename,
                size_bytes=path.stat().st_size,
                sha256=sha256_file(path),
                mime_type=mime_type,
                local_relative_path=filename,
                target_relative_path=filename,
            )
        )
    manifest_path = output_dir / f"{base_name}.manifest.json"
    write_deterministic_json(
        manifest_path,
        {
            "schema_version": "1.0",
            "package_id": package.id,
            "approvers": {"metadata": "operator@example.test", "qa": None},
            "files": [item.model_dump(mode="json") for item in files],
        },
    )
    builds = SQLAlchemyPackageBuildRepository(database.session_factory)
    build = builds.create(
        package_id=package.id,
        master_build_id="master-build-1",
        transcription_run_id="transcription-1",
        metadata_version_id="metadata-1",
        delivery_id=f"local-{package.id}",
        customer="internal",
        schema_version="1.0",
        base_name=base_name,
        build_parameters={},
    )
    build = builds.succeed(
        build.id,
        output_dir=str(output_dir),
        files=files,
        manifest_path=str(manifest_path),
        manifest_sha256=sha256_file(manifest_path),
        computed_metadata=DeliveryMetadata.model_validate(
            {
                "Title": "Example",
                "Description": "Example package",
                "Publication Date": None,
                "Category": "Other",
                "Duration": 1.0,
                "Language": "en",
                "Resolution": "1920x1080",
                "WPM": 60.0,
            }
        ),
    )
    reviews = SQLAlchemyQARepository(database.session_factory)
    return database, packages, builds, reviews, build


def test_approved_build_is_finalized_and_exported_as_streaming_zip(
    tmp_path: Path,
) -> None:
    database, packages, builds, reviews, build = prepare_package_build(tmp_path)
    qa = QAService(packages=packages, builds=builds, reviews=reviews)

    review = qa.review(
        build.package_id,
        QAReviewRequest(
            package_build_id=build.id,
            approved=True,
            reviewer="qa@example.test",
        ),
    )

    assert review.decision is QADecision.APPROVED
    assert packages.get(build.package_id).state is PackageState.VALIDATED
    finalized_build = builds.get(build.id)
    assert finalized_build.manifest_sha256 == review.manifest_sha256
    assert finalized_build.manifest_path is not None
    manifest = json.loads(Path(finalized_build.manifest_path).read_text())
    assert manifest["approvers"]["qa"] == "qa@example.test"
    assert manifest["qa_review"]["decision"] == "approved"

    exports = SQLAlchemyExportRepository(database.session_factory)
    exporter = LocalExportService(
        packages=packages,
        builds=builds,
        reviews=reviews,
        exports=exports,
        export_dir=tmp_path / "exports",
        chunk_size=64 * 1024,
    )
    exported = exporter.export(build.package_id, build.id)

    assert exported.state is ExportState.SUCCEEDED
    assert exported.archive_sha256 is not None
    assert exported.archive_path is not None
    assert sha256_file(Path(exported.archive_path)) == exported.archive_sha256
    with ZipFile(exported.archive_path) as archive:
        assert archive.namelist() == [
            *(item.target_relative_path for item in finalized_build.files),
            Path(finalized_build.manifest_path).name,
        ]

    archive_sha256 = exported.archive_sha256
    with pytest.raises(ExportAlreadyExists):
        exporter.export(build.package_id, build.id)
    assert sha256_file(Path(exported.archive_path)) == archive_sha256
    assert [item.state for item in exports.list_for_build(build.id)] == [
        ExportState.SUCCEEDED,
        ExportState.FAILED,
    ]


def test_qa_rejects_artifact_changed_after_automatic_validation(tmp_path: Path) -> None:
    _, packages, builds, reviews, build = prepare_package_build(tmp_path)
    assert build.output_dir is not None
    Path(build.output_dir, build.files[0].filename).write_bytes(b"changed")
    qa = QAService(packages=packages, builds=builds, reviews=reviews)

    with pytest.raises(QAArtifactMismatch):
        qa.review(
            build.package_id,
            QAReviewRequest(
                package_build_id=build.id,
                approved=True,
                reviewer="qa@example.test",
            ),
        )

    assert packages.get(build.package_id).state is PackageState.AWAITING_QA
    assert reviews.list_for_package(build.package_id) == []


def test_export_rechecks_files_after_qa_approval(tmp_path: Path) -> None:
    database, packages, builds, reviews, build = prepare_package_build(tmp_path)
    qa = QAService(packages=packages, builds=builds, reviews=reviews)
    qa.review(
        build.package_id,
        QAReviewRequest(
            package_build_id=build.id,
            approved=True,
            reviewer="qa@example.test",
        ),
    )
    finalized = builds.get(build.id)
    assert finalized.output_dir is not None
    Path(finalized.output_dir, finalized.files[1].filename).write_bytes(b"tampered")
    exports = SQLAlchemyExportRepository(database.session_factory)
    exporter = LocalExportService(
        packages=packages,
        builds=builds,
        reviews=reviews,
        exports=exports,
        export_dir=tmp_path / "exports",
        chunk_size=64 * 1024,
    )

    with pytest.raises(ExportWorkflowError):
        exporter.export(build.package_id, build.id)

    failed = exports.list_for_build(build.id)[0]
    assert failed.state is ExportState.FAILED


def test_rejection_requires_reason() -> None:
    with pytest.raises(ValidationError):
        QAReviewRequest(
            package_build_id="build-1",
            approved=False,
            reviewer="qa@example.test",
        )


def test_delivery_uploads_media_first_and_verifies_complete_package(tmp_path: Path) -> None:
    database, packages, builds, reviews, build = prepare_package_build(tmp_path)
    QAService(packages=packages, builds=builds, reviews=reviews).review(
        build.package_id,
        QAReviewRequest(
            package_build_id=build.id,
            approved=True,
            reviewer="qa@example.test",
        ),
    )
    repository = SQLAlchemyDeliveryRepository(database.session_factory)
    provider = MemoryDeliveryProvider()
    service = DeliveryService(
        packages=packages,
        builds=builds,
        reviews=reviews,
        deliveries=repository,
        provider=provider,
    )
    delivery = service.create(
        build.package_id,
        DeliveryCreateRequest(package_build_id=build.id, prefix="2026/test"),
        idempotency_key="delivery-1",
    )

    completed = service.deliver(delivery.id)

    assert completed.state is DeliveryState.COMPLETE
    assert packages.get(build.package_id).state is PackageState.COMPLETE
    assert completed.media_uploaded_at is not None
    assert completed.sidecars_uploaded_at is not None
    assert completed.package_complete_at is not None
    assert provider.uploaded_keys[0].endswith(".mp4")
    assert all(item.verified_at is not None for item in repository.list_objects(delivery.id))


def test_completed_delivery_can_still_be_exported(tmp_path: Path) -> None:
    database, packages, builds, reviews, build = prepare_package_build(tmp_path)
    QAService(packages=packages, builds=builds, reviews=reviews).review(
        build.package_id,
        QAReviewRequest(
            package_build_id=build.id,
            approved=True,
            reviewer="qa@example.test",
        ),
    )
    delivery_service = DeliveryService(
        packages=packages,
        builds=builds,
        reviews=reviews,
        deliveries=SQLAlchemyDeliveryRepository(database.session_factory),
        provider=MemoryDeliveryProvider(),
    )
    delivery = delivery_service.create(
        build.package_id,
        DeliveryCreateRequest(package_build_id=build.id),
        idempotency_key="delivery-before-export",
    )
    delivery_service.deliver(delivery.id)
    assert packages.get(build.package_id).state is PackageState.COMPLETE

    exported = LocalExportService(
        packages=packages,
        builds=builds,
        reviews=reviews,
        exports=SQLAlchemyExportRepository(database.session_factory),
        export_dir=tmp_path / "exports",
        chunk_size=64 * 1024,
    ).export(build.package_id, build.id)

    assert exported.state is ExportState.SUCCEEDED
    assert exported.archive_path is not None
    assert Path(exported.archive_path).is_file()


def test_delivery_retry_resumes_after_media_without_reuploading_it(tmp_path: Path) -> None:
    database, packages, builds, reviews, build = prepare_package_build(tmp_path)
    QAService(packages=packages, builds=builds, reviews=reviews).review(
        build.package_id,
        QAReviewRequest(
            package_build_id=build.id,
            approved=True,
            reviewer="qa@example.test",
        ),
    )
    repository = SQLAlchemyDeliveryRepository(database.session_factory)
    provider = MemoryDeliveryProvider()
    provider.fail_suffix = ".transcript.json"
    service = DeliveryService(
        packages=packages,
        builds=builds,
        reviews=reviews,
        deliveries=repository,
        provider=provider,
    )
    delivery = service.create(
        build.package_id,
        DeliveryCreateRequest(package_build_id=build.id),
        idempotency_key="delivery-retry",
    )

    with pytest.raises(RuntimeError, match="interrupted"):
        service.deliver(delivery.id)

    assert repository.get(delivery.id).state is DeliveryState.FAILED
    assert packages.get(build.package_id).state is PackageState.DELIVERY_FAILED
    master_key = provider.uploaded_keys[0]
    provider.fail_suffix = None
    service.retry(delivery.id)
    completed = service.deliver(delivery.id)

    assert completed.state is DeliveryState.COMPLETE
    assert provider.uploaded_keys.count(master_key) == 1
