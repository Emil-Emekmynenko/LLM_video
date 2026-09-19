from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.errors import EntityNotFoundError, VersionConflictError
from media_factory.domain.package import Package
from media_factory.domain.package_state import PackageState
from media_factory.persistence.tables import AssetRow, PackageRow, utc_now


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
            result = cast(CursorResult[Any], session.execute(statement))
            if result.rowcount != 1:
                if session.get(PackageRow, package_id) is None:
                    raise EntityNotFoundError("package", package_id)
                raise VersionConflictError("package", package_id, expected_version)

            refreshed_statement: Select[tuple[PackageRow]] = select(PackageRow).where(
                PackageRow.id == package_id
            )
            row = session.scalar(refreshed_statement)
            if row is None:
                raise EntityNotFoundError("package", package_id)
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
