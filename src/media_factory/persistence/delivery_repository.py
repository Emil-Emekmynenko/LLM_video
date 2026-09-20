from datetime import datetime
from uuid import uuid4

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.delivery import DeliveryAttempt, DeliveryState, UploadedObject
from media_factory.domain.errors import EntityNotFoundError, IdempotencyConflictError
from media_factory.domain.package_state import PackageState, ensure_transition_allowed
from media_factory.persistence.tables import (
    DeliveryAttemptRow,
    PackageRow,
    UploadCheckpointRow,
    UploadedObjectRow,
    utc_now,
)
from media_factory.providers.object_storage import TransferCheckpoint
from media_factory.services.checkpoint_crypto import CheckpointCipher


class DeliveryStateConflict(RuntimeError):
    code = "delivery_state_conflict"


class SQLAlchemyDeliveryRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        checkpoint_cipher: CheckpointCipher | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.checkpoint_cipher = checkpoint_cipher

    @property
    def checkpoints_enabled(self) -> bool:
        return self.checkpoint_cipher is not None

    def create_or_get(
        self,
        *,
        package_id: str,
        package_build_id: str,
        provider: str,
        destination: str,
        prefix: str,
        idempotency_key: str,
    ) -> tuple[DeliveryAttempt, bool]:
        with self.session_factory() as session:
            existing = self._find_by_idempotency(session, package_id, idempotency_key)
            if existing is not None:
                self._verify_same_request(
                    existing,
                    package_build_id=package_build_id,
                    provider=provider,
                    destination=destination,
                    prefix=prefix,
                )
                return self._to_domain(existing), False
            existing_build = session.scalar(
                select(DeliveryAttemptRow).where(
                    DeliveryAttemptRow.package_build_id == package_build_id
                )
            )
            if existing_build is not None:
                raise DeliveryStateConflict("package build already has a delivery attempt")
            package = session.get(PackageRow, package_id)
            if package is None:
                raise EntityNotFoundError("package", package_id)
            if PackageState(package.state) is not PackageState.VALIDATED:
                raise DeliveryStateConflict("package must be validated before delivery")
            ensure_transition_allowed(PackageState(package.state), PackageState.DELIVERY_QUEUED)
            row = DeliveryAttemptRow(
                id=str(uuid4()),
                package_id=package_id,
                package_build_id=package_build_id,
                provider=provider,
                destination=destination,
                prefix=prefix,
                state=DeliveryState.QUEUED.value,
                idempotency_key=idempotency_key,
            )
            session.add(row)
            package.state = PackageState.DELIVERY_QUEUED.value
            package.version += 1
            package.updated_at = utc_now()
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raced = self._find_by_idempotency(session, package_id, idempotency_key)
                if raced is None:
                    existing_build = session.scalar(
                        select(DeliveryAttemptRow).where(
                            DeliveryAttemptRow.package_build_id == package_build_id
                        )
                    )
                    if existing_build is not None:
                        raise DeliveryStateConflict(
                            "package build already has a delivery attempt"
                        ) from None
                    raise
                self._verify_same_request(
                    raced,
                    package_build_id=package_build_id,
                    provider=provider,
                    destination=destination,
                    prefix=prefix,
                )
                return self._to_domain(raced), False
            session.refresh(row)
            return self._to_domain(row), True

    def get(self, delivery_id: str) -> DeliveryAttempt:
        with self.session_factory() as session:
            row = session.get(DeliveryAttemptRow, delivery_id)
            if row is None:
                raise EntityNotFoundError("delivery", delivery_id)
            return self._to_domain(row)

    def list_attempts(self, package_id: str | None = None) -> list[DeliveryAttempt]:
        statement: Select[tuple[DeliveryAttemptRow]] = select(DeliveryAttemptRow)
        if package_id is not None:
            statement = statement.where(DeliveryAttemptRow.package_id == package_id)
        statement = statement.order_by(DeliveryAttemptRow.created_at.asc())
        with self.session_factory() as session:
            return [self._to_domain(row) for row in session.scalars(statement)]

    def advance(
        self,
        delivery_id: str,
        *,
        expected_delivery: DeliveryState,
        target_delivery: DeliveryState,
        expected_package: PackageState,
        target_package: PackageState,
        timestamp_field: str | None = None,
    ) -> DeliveryAttempt:
        with self.session_factory.begin() as session:
            delivery = session.get(DeliveryAttemptRow, delivery_id)
            if delivery is None:
                raise EntityNotFoundError("delivery", delivery_id)
            package = session.get(PackageRow, delivery.package_id)
            if package is None:
                raise EntityNotFoundError("package", delivery.package_id)
            if (
                DeliveryState(delivery.state) is not expected_delivery
                or PackageState(package.state) is not expected_package
            ):
                raise DeliveryStateConflict("delivery or package state changed concurrently")
            ensure_transition_allowed(expected_package, target_package)
            now = utc_now()
            delivery.state = target_delivery.value
            delivery.updated_at = now
            if timestamp_field is not None:
                setattr(delivery, timestamp_field, now)
            package.state = target_package.value
            package.version += 1
            package.updated_at = now
            session.flush()
            session.refresh(delivery)
            return self._to_domain(delivery)

    def fail(self, delivery_id: str, *, code: str, message: str) -> DeliveryAttempt:
        with self.session_factory.begin() as session:
            delivery = session.get(DeliveryAttemptRow, delivery_id)
            if delivery is None:
                raise EntityNotFoundError("delivery", delivery_id)
            package = session.get(PackageRow, delivery.package_id)
            if package is None:
                raise EntityNotFoundError("package", delivery.package_id)
            current = PackageState(package.state)
            if current is not PackageState.DELIVERY_FAILED:
                ensure_transition_allowed(current, PackageState.DELIVERY_FAILED)
                package.state = PackageState.DELIVERY_FAILED.value
                package.version += 1
                package.updated_at = utc_now()
            now = utc_now()
            delivery.state = DeliveryState.FAILED.value
            delivery.delivery_failed_at = now
            delivery.error_code = code
            delivery.error_message = message
            delivery.updated_at = now
            session.flush()
            session.refresh(delivery)
            return self._to_domain(delivery)

    def retry(self, delivery_id: str) -> DeliveryAttempt:
        with self.session_factory.begin() as session:
            delivery = session.get(DeliveryAttemptRow, delivery_id)
            if delivery is None:
                raise EntityNotFoundError("delivery", delivery_id)
            package = session.get(PackageRow, delivery.package_id)
            if package is None:
                raise EntityNotFoundError("package", delivery.package_id)
            if (
                DeliveryState(delivery.state) is not DeliveryState.FAILED
                or PackageState(package.state) is not PackageState.DELIVERY_FAILED
            ):
                raise DeliveryStateConflict("only a failed delivery can be retried")
            ensure_transition_allowed(PackageState.DELIVERY_FAILED, PackageState.DELIVERY_QUEUED)
            now = utc_now()
            delivery.state = DeliveryState.QUEUED.value
            delivery.delivery_failed_at = None
            delivery.error_code = None
            delivery.error_message = None
            delivery.updated_at = now
            package.state = PackageState.DELIVERY_QUEUED.value
            package.version += 1
            package.updated_at = now
            session.flush()
            session.refresh(delivery)
            return self._to_domain(delivery)

    def record_object(
        self,
        *,
        delivery_attempt_id: str,
        role: str,
        local_relative_path: str,
        remote_key: str,
        size_bytes: int,
        sha256: str,
        provider_checksum: str | None,
    ) -> UploadedObject:
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(UploadedObjectRow)
                .where(UploadedObjectRow.delivery_attempt_id == delivery_attempt_id)
                .where(UploadedObjectRow.remote_key == remote_key)
            )
            if existing is not None:
                if existing.size_bytes != size_bytes or existing.sha256 != sha256:
                    raise DeliveryStateConflict("recorded remote object does not match source")
                return self._object_to_domain(existing)
            row = UploadedObjectRow(
                id=str(uuid4()),
                delivery_attempt_id=delivery_attempt_id,
                role=role,
                local_relative_path=local_relative_path,
                remote_key=remote_key,
                size_bytes=size_bytes,
                sha256=sha256,
                provider_checksum=provider_checksum,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._object_to_domain(row)

    def list_objects(self, delivery_id: str) -> list[UploadedObject]:
        statement: Select[tuple[UploadedObjectRow]] = (
            select(UploadedObjectRow)
            .where(UploadedObjectRow.delivery_attempt_id == delivery_id)
            .order_by(UploadedObjectRow.uploaded_at.asc())
        )
        with self.session_factory() as session:
            return [self._object_to_domain(row) for row in session.scalars(statement)]

    def mark_verified(self, object_id: str, verified_at: datetime) -> UploadedObject:
        with self.session_factory.begin() as session:
            row = session.get(UploadedObjectRow, object_id)
            if row is None:
                raise EntityNotFoundError("uploaded_object", object_id)
            row.verified_at = verified_at
            session.flush()
            session.refresh(row)
            return self._object_to_domain(row)

    def get_checkpoint(
        self,
        *,
        delivery_attempt_id: str,
        remote_key: str,
        provider: str,
        source_size_bytes: int,
        source_sha256: str,
    ) -> TransferCheckpoint | None:
        cipher = self._require_checkpoint_cipher()
        with self.session_factory() as session:
            row = session.scalar(
                select(UploadCheckpointRow)
                .where(UploadCheckpointRow.delivery_attempt_id == delivery_attempt_id)
                .where(UploadCheckpointRow.remote_key == remote_key)
            )
            if row is None:
                return None
            if (
                row.provider != provider
                or row.source_size_bytes != source_size_bytes
                or row.source_sha256 != source_sha256
            ):
                raise DeliveryStateConflict("upload checkpoint does not match delivery source")
            return TransferCheckpoint(
                session_token=cipher.decrypt(row.encrypted_session_token),
                next_offset=row.next_offset,
                completed_parts=list(row.completed_parts),
            )

    def save_checkpoint(
        self,
        *,
        delivery_attempt_id: str,
        remote_key: str,
        provider: str,
        source_size_bytes: int,
        source_sha256: str,
        checkpoint: TransferCheckpoint,
    ) -> None:
        cipher = self._require_checkpoint_cipher()
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(UploadCheckpointRow)
                .where(UploadCheckpointRow.delivery_attempt_id == delivery_attempt_id)
                .where(UploadCheckpointRow.remote_key == remote_key)
            )
            now = utc_now()
            if row is None:
                row = UploadCheckpointRow(
                    id=str(uuid4()),
                    delivery_attempt_id=delivery_attempt_id,
                    remote_key=remote_key,
                    provider=provider,
                    source_size_bytes=source_size_bytes,
                    source_sha256=source_sha256,
                    encrypted_session_token=cipher.encrypt(checkpoint.session_token),
                    next_offset=checkpoint.next_offset,
                    completed_parts=checkpoint.completed_parts,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                if (
                    row.provider != provider
                    or row.source_size_bytes != source_size_bytes
                    or row.source_sha256 != source_sha256
                ):
                    raise DeliveryStateConflict("upload checkpoint does not match delivery source")
                row.encrypted_session_token = cipher.encrypt(checkpoint.session_token)
                row.next_offset = checkpoint.next_offset
                row.completed_parts = checkpoint.completed_parts
                row.updated_at = now

    def delete_checkpoint(self, *, delivery_attempt_id: str, remote_key: str) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(UploadCheckpointRow)
                .where(UploadCheckpointRow.delivery_attempt_id == delivery_attempt_id)
                .where(UploadCheckpointRow.remote_key == remote_key)
            )
            if row is not None:
                session.delete(row)

    def _require_checkpoint_cipher(self) -> CheckpointCipher:
        if self.checkpoint_cipher is None:
            raise DeliveryStateConflict("persistent upload checkpoints are not configured")
        return self.checkpoint_cipher

    @staticmethod
    def _find_by_idempotency(
        session: Session, package_id: str, idempotency_key: str
    ) -> DeliveryAttemptRow | None:
        return session.scalar(
            select(DeliveryAttemptRow)
            .where(DeliveryAttemptRow.package_id == package_id)
            .where(DeliveryAttemptRow.idempotency_key == idempotency_key)
        )

    @staticmethod
    def _verify_same_request(
        row: DeliveryAttemptRow,
        *,
        package_build_id: str,
        provider: str,
        destination: str,
        prefix: str,
    ) -> None:
        if (
            row.package_build_id != package_build_id
            or row.provider != provider
            or row.destination != destination
            or row.prefix != prefix
        ):
            raise IdempotencyConflictError(row.idempotency_key)

    @staticmethod
    def _to_domain(row: DeliveryAttemptRow) -> DeliveryAttempt:
        return DeliveryAttempt(
            id=row.id,
            package_id=row.package_id,
            package_build_id=row.package_build_id,
            provider=row.provider,
            destination=row.destination,
            prefix=row.prefix,
            state=DeliveryState(row.state),
            idempotency_key=row.idempotency_key,
            delivered_at=row.delivered_at,
            media_uploaded_at=row.media_uploaded_at,
            sidecars_uploaded_at=row.sidecars_uploaded_at,
            package_complete_at=row.package_complete_at,
            delivery_failed_at=row.delivery_failed_at,
            error_code=row.error_code,
            error_message=row.error_message,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _object_to_domain(row: UploadedObjectRow) -> UploadedObject:
        return UploadedObject(
            id=row.id,
            delivery_attempt_id=row.delivery_attempt_id,
            role=row.role,
            local_relative_path=row.local_relative_path,
            remote_key=row.remote_key,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            provider_checksum=row.provider_checksum,
            uploaded_at=row.uploaded_at,
            verified_at=row.verified_at,
        )
