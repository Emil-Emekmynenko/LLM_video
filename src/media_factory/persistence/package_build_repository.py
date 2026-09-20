from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.errors import EntityNotFoundError
from media_factory.domain.models import ValidationIssue
from media_factory.domain.packaging import (
    DeliveryMetadata,
    PackageBuild,
    PackageBuildState,
    PackageFile,
)
from media_factory.persistence.tables import PackageBuildRow, utc_now


class SQLAlchemyPackageBuildRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(
        self,
        *,
        package_id: str,
        master_build_id: str,
        transcription_run_id: str,
        metadata_version_id: str,
        delivery_id: str,
        customer: str,
        schema_version: str,
        base_name: str,
        build_parameters: dict[str, Any],
    ) -> PackageBuild:
        with self.session_factory.begin() as session:
            version = (
                int(
                    session.scalar(
                        select(func.max(PackageBuildRow.version)).where(
                            PackageBuildRow.package_id == package_id
                        )
                    )
                    or 0
                )
                + 1
            )
            row = PackageBuildRow(
                id=str(uuid4()),
                package_id=package_id,
                version=version,
                state=PackageBuildState.RUNNING.value,
                master_build_id=master_build_id,
                transcription_run_id=transcription_run_id,
                metadata_version_id=metadata_version_id,
                delivery_id=delivery_id,
                customer=customer,
                schema_version=schema_version,
                base_name=base_name,
                files=[],
                validation_issues=[],
                build_parameters=build_parameters,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._to_domain(row)

    def succeed(
        self,
        build_id: str,
        *,
        output_dir: str,
        files: list[PackageFile],
        manifest_path: str,
        manifest_sha256: str,
        computed_metadata: DeliveryMetadata,
    ) -> PackageBuild:
        return self._update(
            build_id,
            state=PackageBuildState.SUCCEEDED.value,
            output_dir=output_dir,
            files=[item.model_dump(mode="json") for item in files],
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            computed_metadata=computed_metadata.model_dump(mode="json", by_alias=True),
            validation_issues=[],
            finished_at=utc_now(),
        )

    def fail(
        self,
        build_id: str,
        *,
        code: str,
        message: str,
        issues: list[ValidationIssue],
        output_dir: str | None = None,
        files: list[PackageFile] | None = None,
        manifest_path: str | None = None,
        manifest_sha256: str | None = None,
        computed_metadata: DeliveryMetadata | None = None,
    ) -> PackageBuild:
        return self._update(
            build_id,
            state=PackageBuildState.FAILED.value,
            output_dir=output_dir,
            error_code=code,
            error_message=message,
            validation_issues=[issue.model_dump(mode="json") for issue in issues],
            files=[item.model_dump(mode="json") for item in files or []],
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            computed_metadata=(
                computed_metadata.model_dump(mode="json", by_alias=True)
                if computed_metadata is not None
                else None
            ),
            finished_at=utc_now(),
        )

    def get(self, build_id: str) -> PackageBuild:
        with self.session_factory() as session:
            row = session.get(PackageBuildRow, build_id)
            if row is None:
                raise EntityNotFoundError("package_build", build_id)
            return self._to_domain(row)

    def list_for_package(self, package_id: str) -> list[PackageBuild]:
        statement: Select[tuple[PackageBuildRow]] = (
            select(PackageBuildRow)
            .where(PackageBuildRow.package_id == package_id)
            .order_by(PackageBuildRow.version.asc())
        )
        with self.session_factory() as session:
            return [self._to_domain(row) for row in session.scalars(statement)]

    def _update(self, build_id: str, **values: Any) -> PackageBuild:
        with self.session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(PackageBuildRow).where(PackageBuildRow.id == build_id).values(**values)
                ),
            )
            if result.rowcount != 1:
                raise EntityNotFoundError("package_build", build_id)
            row = session.get(PackageBuildRow, build_id)
            if row is None:
                raise EntityNotFoundError("package_build", build_id)
            return self._to_domain(row)

    @staticmethod
    def _to_domain(row: PackageBuildRow) -> PackageBuild:
        return PackageBuild(
            id=row.id,
            package_id=row.package_id,
            version=row.version,
            state=PackageBuildState(row.state),
            master_build_id=row.master_build_id,
            transcription_run_id=row.transcription_run_id,
            metadata_version_id=row.metadata_version_id,
            delivery_id=row.delivery_id,
            customer=row.customer,
            schema_version=row.schema_version,
            base_name=row.base_name,
            output_dir=row.output_dir,
            files=[PackageFile.model_validate(item) for item in row.files],
            manifest_path=row.manifest_path,
            manifest_sha256=row.manifest_sha256,
            computed_metadata=(
                DeliveryMetadata.model_validate(row.computed_metadata)
                if row.computed_metadata is not None
                else None
            ),
            validation_issues=[
                ValidationIssue.model_validate(issue) for issue in row.validation_issues
            ],
            build_parameters=row.build_parameters,
            error_code=row.error_code,
            error_message=row.error_message,
            created_at=row.created_at,
            finished_at=row.finished_at,
        )
