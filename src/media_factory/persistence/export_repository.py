from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.errors import EntityNotFoundError
from media_factory.domain.qa import ExportState, LocalExport
from media_factory.persistence.tables import LocalExportRow, utc_now


class SQLAlchemyExportRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(
        self,
        *,
        package_id: str,
        package_build_id: str,
        qa_review_id: str,
    ) -> LocalExport:
        row = LocalExportRow(
            id=str(uuid4()),
            package_id=package_id,
            package_build_id=package_build_id,
            qa_review_id=qa_review_id,
            state=ExportState.RUNNING.value,
        )
        with self.session_factory.begin() as session:
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._to_domain(row)

    def succeed(
        self,
        export_id: str,
        *,
        archive_path: Path,
        archive_size_bytes: int,
        archive_sha256: str,
    ) -> LocalExport:
        return self._update(
            export_id,
            state=ExportState.SUCCEEDED.value,
            archive_path=str(archive_path),
            archive_size_bytes=archive_size_bytes,
            archive_sha256=archive_sha256,
            finished_at=utc_now(),
        )

    def fail(self, export_id: str, *, code: str, message: str) -> LocalExport:
        return self._update(
            export_id,
            state=ExportState.FAILED.value,
            error_code=code,
            error_message=message,
            finished_at=utc_now(),
        )

    def get(self, export_id: str) -> LocalExport:
        with self.session_factory() as session:
            row = session.get(LocalExportRow, export_id)
            if row is None:
                raise EntityNotFoundError("local_export", export_id)
            return self._to_domain(row)

    def list_for_build(self, package_build_id: str) -> list[LocalExport]:
        statement: Select[tuple[LocalExportRow]] = (
            select(LocalExportRow)
            .where(LocalExportRow.package_build_id == package_build_id)
            .order_by(LocalExportRow.created_at.asc())
        )
        with self.session_factory() as session:
            return [self._to_domain(row) for row in session.scalars(statement)]

    def _update(self, export_id: str, **values: Any) -> LocalExport:
        with self.session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(LocalExportRow)
                    .where(LocalExportRow.id == export_id)
                    .values(**values)
                ),
            )
            if result.rowcount != 1:
                raise EntityNotFoundError("local_export", export_id)
            row = session.get(LocalExportRow, export_id)
            if row is None:
                raise EntityNotFoundError("local_export", export_id)
            return self._to_domain(row)

    @staticmethod
    def _to_domain(row: LocalExportRow) -> LocalExport:
        return LocalExport(
            id=row.id,
            package_id=row.package_id,
            package_build_id=row.package_build_id,
            qa_review_id=row.qa_review_id,
            state=ExportState(row.state),
            archive_path=row.archive_path,
            archive_size_bytes=row.archive_size_bytes,
            archive_sha256=row.archive_sha256,
            error_code=row.error_code,
            error_message=row.error_message,
            created_at=row.created_at,
            finished_at=row.finished_at,
        )
