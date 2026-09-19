from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.errors import EntityNotFoundError, VersionConflictError
from media_factory.domain.package import Package
from media_factory.domain.package_state import PackageState
from media_factory.persistence.tables import AssetRow, AuditEventRow, PackageRow, utc_now
from media_factory.services.request_context import get_request_context


class SQLAlchemyPackageRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(self, source_asset_id: str) -> Package:
        with self.session_factory.begin() as session:
            if session.get(AssetRow, source_asset_id) is None:
                raise EntityNotFoundError("asset", source_asset_id)
            row = PackageRow(
                id=str(uuid4()),
                source_asset_id=source_asset_id,
                state=PackageState.UPLOADED.value,
                version=1,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._to_domain(row)

    def get(self, package_id: str) -> Package:
        with self.session_factory() as session:
            row = session.get(PackageRow, package_id)
            if row is None:
                raise EntityNotFoundError("package", package_id)
            return self._to_domain(row)

    def list_packages(self) -> list[Package]:
        statement: Select[tuple[PackageRow]] = select(PackageRow).order_by(
            PackageRow.updated_at.desc(), PackageRow.created_at.desc()
        )
        with self.session_factory() as session:
            return [self._to_domain(row) for row in session.scalars(statement)]

    def transition(
        self,
        package_id: str,
        *,
        target: PackageState,
        expected_version: int,
    ) -> Package:
        statement = (
            update(PackageRow)
            .where(PackageRow.id == package_id)
            .where(PackageRow.version == expected_version)
            .values(
                state=target.value,
                version=PackageRow.version + 1,
                updated_at=utc_now(),
            )
        )
        with self.session_factory.begin() as session:
            current = session.get(PackageRow, package_id)
            if current is None:
                raise EntityNotFoundError("package", package_id)
            previous_state = current.state
            result = cast(CursorResult[Any], session.execute(statement))
            if result.rowcount != 1:
                raise VersionConflictError("package", package_id, expected_version)

            refreshed_statement: Select[tuple[PackageRow]] = select(PackageRow).where(
                PackageRow.id == package_id
            )
            row = session.scalar(refreshed_statement)
            if row is None:
                raise EntityNotFoundError("package", package_id)
            context = get_request_context()
            session.add(
                AuditEventRow(
                    id=str(uuid4()),
                    actor=context.principal.actor,
                    role=context.principal.role.value,
                    action="package.state_changed",
                    entity_type="package",
                    entity_id=package_id,
                    package_id=package_id,
                    correlation_id=context.correlation_id,
                    details={"from": previous_state, "to": target.value},
                )
            )
            return self._to_domain(row)

    @staticmethod
    def _to_domain(row: PackageRow) -> Package:
        return Package(
            id=row.id,
            source_asset_id=row.source_asset_id,
            state=PackageState(row.state),
            version=row.version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
