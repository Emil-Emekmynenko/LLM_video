from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from media_factory.domain.delivery import (
    DeliveryAttempt,
    DeliveryCreateRequest,
    DeliveryState,
    UploadedObject,
)
from media_factory.domain.package_state import PackageState
from media_factory.domain.packaging import PackageBuild, PackageBuildState
from media_factory.domain.qa import QADecision
from media_factory.persistence.delivery_repository import SQLAlchemyDeliveryRepository
from media_factory.persistence.package_build_repository import (
    SQLAlchemyPackageBuildRepository,
)
from media_factory.persistence.package_repository import SQLAlchemyPackageRepository
from media_factory.persistence.qa_repository import SQLAlchemyQARepository
from media_factory.providers.object_storage import ObjectStorageProvider
from media_factory.services.checksum import sha256_file
from media_factory.services.package_artifacts import ensure_safe_relative_path


class DeliveryWorkflowError(RuntimeError):
    code = "delivery_workflow_blocked"


class RemoteVerificationError(RuntimeError):
    code = "remote_verification_failed"


@dataclass(frozen=True)
class DeliveryFile:
    role: str
    source: Path
    local_relative_path: str
    remote_key: str
    size_bytes: int
    sha256: str


class DeliveryService:
    def __init__(
        self,
        *,
        packages: SQLAlchemyPackageRepository,
        builds: SQLAlchemyPackageBuildRepository,
        reviews: SQLAlchemyQARepository,
        deliveries: SQLAlchemyDeliveryRepository,
        provider: ObjectStorageProvider,
    ) -> None:
        self.packages = packages
        self.builds = builds
        self.reviews = reviews
        self.deliveries = deliveries
        self.provider = provider

    def create(
        self,
        package_id: str,
        request: DeliveryCreateRequest,
        *,
        idempotency_key: str,
    ) -> DeliveryAttempt:
        self._validated_build(package_id, request.package_build_id)
        delivery, _ = self.deliveries.create_or_get(
            package_id=package_id,
            package_build_id=request.package_build_id,
            provider=self.provider.name,
            destination=self.provider.destination,
            prefix=request.prefix,
            idempotency_key=idempotency_key,
        )
        return delivery

    def retry(self, delivery_id: str) -> DeliveryAttempt:
        delivery = self.deliveries.get(delivery_id)
        if delivery.state is DeliveryState.QUEUED:
            return delivery
        self._validated_build(delivery.package_id, delivery.package_build_id, allow_failed=True)
        return self.deliveries.retry(delivery_id)

    def validate_job(self, package_id: str, delivery_id: str) -> DeliveryAttempt:
        delivery = self.deliveries.get(delivery_id)
        if delivery.package_id != package_id or delivery.state is not DeliveryState.QUEUED:
            raise DeliveryWorkflowError("delivery is not queued for this package")
        self._validated_build(package_id, delivery.package_build_id, allow_queued=True)
        return delivery

    def deliver(self, delivery_id: str) -> DeliveryAttempt:
        delivery = self.deliveries.get(delivery_id)
        try:
            build = self._validated_build(
                delivery.package_id,
                delivery.package_build_id,
                allow_queued=True,
            )
            files = self._delivery_files(build, delivery.prefix)
            delivery = self.deliveries.advance(
                delivery.id,
                expected_delivery=DeliveryState.QUEUED,
                target_delivery=DeliveryState.UPLOADING_MEDIA,
                expected_package=PackageState.DELIVERY_QUEUED,
                target_package=PackageState.UPLOADING_MEDIA,
            )
            media = [item for item in files if item.role == "master"]
            if len(media) != 1:
                raise DeliveryWorkflowError("delivery requires exactly one master file")
            self._upload(delivery.id, media)
            delivery = self.deliveries.advance(
                delivery.id,
                expected_delivery=DeliveryState.UPLOADING_MEDIA,
                target_delivery=DeliveryState.UPLOADING_SIDECARS,
                expected_package=PackageState.UPLOADING_MEDIA,
                target_package=PackageState.UPLOADING_SIDECARS,
                timestamp_field="media_uploaded_at",
            )
            self._upload(delivery.id, [item for item in files if item.role != "master"])
            delivery = self.deliveries.advance(
                delivery.id,
                expected_delivery=DeliveryState.UPLOADING_SIDECARS,
                target_delivery=DeliveryState.VERIFYING,
                expected_package=PackageState.UPLOADING_SIDECARS,
                target_package=PackageState.VERIFYING_DELIVERY,
                timestamp_field="sidecars_uploaded_at",
            )
            self._verify(delivery.id, files)
            return self.deliveries.advance(
                delivery.id,
                expected_delivery=DeliveryState.VERIFYING,
                target_delivery=DeliveryState.COMPLETE,
                expected_package=PackageState.VERIFYING_DELIVERY,
                target_package=PackageState.COMPLETE,
                timestamp_field="package_complete_at",
            )
        except Exception as exc:
            try:
                self.deliveries.fail(
                    delivery.id,
                    code=str(getattr(exc, "code", "delivery_failed")),
                    message=str(exc),
                )
            except Exception:
                pass
            raise

    def _validated_build(
        self,
        package_id: str,
        package_build_id: str,
        *,
        allow_queued: bool = False,
        allow_failed: bool = False,
    ) -> PackageBuild:
        package = self.packages.get(package_id)
        allowed_states = {PackageState.VALIDATED}
        if allow_queued:
            allowed_states.add(PackageState.DELIVERY_QUEUED)
        if allow_failed:
            allowed_states.add(PackageState.DELIVERY_FAILED)
        if package.state not in allowed_states:
            raise DeliveryWorkflowError("package is not ready for delivery")
        build = self.builds.get(package_build_id)
        if build.package_id != package_id or build.state is not PackageBuildState.SUCCEEDED:
            raise DeliveryWorkflowError("package build is not deliverable")
        package_builds = self.builds.list_for_package(package_id)
        if not package_builds or package_builds[-1].id != build.id:
            raise DeliveryWorkflowError("only the latest package build can be delivered")
        review = self.reviews.get_for_build(build.id)
        if review.decision is not QADecision.APPROVED:
            raise DeliveryWorkflowError("package build is not approved by QA")
        if build.manifest_sha256 != review.manifest_sha256:
            raise DeliveryWorkflowError("approved manifest checksum changed")
        self._delivery_files(build, "")
        return build

    def _delivery_files(self, build: PackageBuild, prefix: str) -> list[DeliveryFile]:
        if build.output_dir is None or build.manifest_path is None or build.manifest_sha256 is None:
            raise DeliveryWorkflowError("package build artifacts are incomplete")
        output_dir = Path(build.output_dir)
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise DeliveryWorkflowError("package directory is missing or unsafe")
        resolved_output = output_dir.resolve()
        result: list[DeliveryFile] = []
        for item in build.files:
            source = output_dir / item.local_relative_path
            self._verify_source(source, resolved_output, item.size_bytes, item.sha256)
            result.append(
                DeliveryFile(
                    role=item.role,
                    source=source,
                    local_relative_path=item.local_relative_path,
                    remote_key=self._remote_key(prefix, item.target_relative_path),
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                )
            )
        manifest = Path(build.manifest_path)
        self._verify_source(
            manifest,
            resolved_output,
            manifest.stat().st_size if manifest.is_file() else -1,
            build.manifest_sha256,
        )
        result.append(
            DeliveryFile(
                role="manifest",
                source=manifest,
                local_relative_path=manifest.name,
                remote_key=self._remote_key(prefix, manifest.name),
                size_bytes=manifest.stat().st_size,
                sha256=build.manifest_sha256,
            )
        )
        if len({item.remote_key for item in result}) != len(result):
            raise DeliveryWorkflowError("delivery contains duplicate remote keys")
        return result

    def _upload(self, delivery_id: str, files: list[DeliveryFile]) -> None:
        recorded = {
            item.remote_key: item for item in self.deliveries.list_objects(delivery_id)
        }
        for item in files:
            existing = recorded.get(item.remote_key)
            if existing is not None:
                self._verify_remote(existing, item)
                continue
            remote = self.provider.upload_if_absent(
                item.source,
                item.remote_key,
                expected_size=item.size_bytes,
                expected_sha256=item.sha256,
            )
            self.deliveries.record_object(
                delivery_attempt_id=delivery_id,
                role=item.role,
                local_relative_path=item.local_relative_path,
                remote_key=item.remote_key,
                size_bytes=item.size_bytes,
                sha256=item.sha256,
                provider_checksum=remote.provider_checksum,
            )

    def _verify(self, delivery_id: str, files: list[DeliveryFile]) -> None:
        records = {
            item.remote_key: item for item in self.deliveries.list_objects(delivery_id)
        }
        for item in files:
            record = records.get(item.remote_key)
            if record is None:
                raise RemoteVerificationError(f"upload record is missing: {item.remote_key}")
            self._verify_remote(record, item)
            self.deliveries.mark_verified(record.id, datetime.now(UTC))

    def _verify_remote(self, record: UploadedObject, item: DeliveryFile) -> None:
        if record.size_bytes != item.size_bytes or record.sha256 != item.sha256:
            raise RemoteVerificationError(f"upload record mismatch: {item.remote_key}")
        remote = self.provider.inspect(item.remote_key)
        if remote.size_bytes != item.size_bytes or remote.sha256 != item.sha256:
            raise RemoteVerificationError(f"remote checksum mismatch: {item.remote_key}")

    @staticmethod
    def _verify_source(
        source: Path,
        resolved_output: Path,
        expected_size: int,
        expected_sha256: str,
    ) -> None:
        if (
            not source.is_file()
            or source.is_symlink()
            or not source.resolve().is_relative_to(resolved_output)
        ):
            raise DeliveryWorkflowError(f"delivery source is missing or unsafe: {source.name}")
        if source.stat().st_size != expected_size or sha256_file(source) != expected_sha256:
            raise DeliveryWorkflowError(f"delivery source changed: {source.name}")

    @staticmethod
    def _remote_key(prefix: str, relative_path: str) -> str:
        ensure_safe_relative_path(relative_path)
        key = str(PurePosixPath(prefix) / relative_path) if prefix else relative_path
        ensure_safe_relative_path(key)
        return key
