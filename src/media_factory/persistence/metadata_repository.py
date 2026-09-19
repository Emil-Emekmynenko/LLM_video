from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.errors import EntityNotFoundError, VersionConflictError
from media_factory.domain.metadata import (
    MetadataCategory,
    MetadataChapter,
    MetadataContent,
    MetadataStatus,
    MetadataVersion,
)
from media_factory.persistence.tables import (
    MetadataCategoryRow,
    MetadataVersionRow,
    utc_now,
)

DEFAULT_CATEGORIES = (
    MetadataCategory(code="Repairs_And_DIY", label="Repairs and DIY"),
    MetadataCategory(code="Cooking", label="Cooking"),
    MetadataCategory(code="Arts_And_Crafts", label="Arts and Crafts"),
    MetadataCategory(code="Technology", label="Technology"),
    MetadataCategory(code="Nature", label="Nature"),
    MetadataCategory(code="Sports_And_Fitness", label="Sports and Fitness"),
    MetadataCategory(code="Other", label="Other"),
)


class SQLAlchemyMetadataRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def ensure_default_categories(self) -> None:
        with self.session_factory.begin() as session:
            for category in DEFAULT_CATEGORIES:
                if session.get(MetadataCategoryRow, category.code) is None:
                    session.add(
                        MetadataCategoryRow(
                            code=category.code,
                            label=category.label,
                            active=True,
                        )
                    )

    def list_categories(self) -> list[MetadataCategory]:
        statement: Select[tuple[MetadataCategoryRow]] = (
            select(MetadataCategoryRow)
            .where(MetadataCategoryRow.active.is_(True))
            .order_by(MetadataCategoryRow.code.asc())
        )
        with self.session_factory() as session:
            return [
                MetadataCategory(code=row.code, label=row.label)
                for row in session.scalars(statement)
            ]

    def create(
        self,
        *,
        package_id: str,
        analysis_run_id: str,
        content: MetadataContent,
        created_by: str,
        change_note: str | None = None,
    ) -> MetadataVersion:
        with self.session_factory.begin() as session:
            self._require_category(session, content.category)
            version = self._next_version(session, package_id)
            session.execute(
                update(MetadataVersionRow)
                .where(MetadataVersionRow.package_id == package_id)
                .where(MetadataVersionRow.status == MetadataStatus.DRAFT.value)
                .values(status=MetadataStatus.SUPERSEDED.value)
            )
            row = self._new_row(
                package_id=package_id,
                analysis_run_id=analysis_run_id,
                version=version,
                content=content,
                created_by=created_by,
                change_note=change_note,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._to_domain(row)

    def revise(
        self,
        metadata_id: str,
        *,
        base_version: int,
        content: MetadataContent,
        created_by: str,
        change_note: str | None,
    ) -> MetadataVersion:
        with self.session_factory.begin() as session:
            base = session.get(MetadataVersionRow, metadata_id)
            if base is None:
                raise EntityNotFoundError("metadata_version", metadata_id)
            latest = self._latest_row(session, base.package_id)
            if (
                base.version != base_version
                or latest.id != base.id
                or base.status != MetadataStatus.DRAFT.value
            ):
                raise VersionConflictError("metadata_version", metadata_id, base_version)
            self._require_category(session, content.category)
            base.status = MetadataStatus.SUPERSEDED.value
            row = self._new_row(
                package_id=base.package_id,
                analysis_run_id=base.analysis_run_id,
                version=base.version + 1,
                content=content,
                created_by=created_by,
                change_note=change_note,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._to_domain(row)

    def approve(self, metadata_id: str, *, expected_version: int) -> MetadataVersion:
        with self.session_factory.begin() as session:
            row = session.get(MetadataVersionRow, metadata_id)
            if row is None:
                raise EntityNotFoundError("metadata_version", metadata_id)
            latest = self._latest_row(session, row.package_id)
            if (
                row.version != expected_version
                or latest.id != row.id
                or row.status != MetadataStatus.DRAFT.value
            ):
                raise VersionConflictError("metadata_version", metadata_id, expected_version)
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(MetadataVersionRow)
                    .where(MetadataVersionRow.id == metadata_id)
                    .where(MetadataVersionRow.status == MetadataStatus.DRAFT.value)
                    .values(status=MetadataStatus.APPROVED.value, approved_at=utc_now())
                ),
            )
            if result.rowcount != 1:
                raise VersionConflictError("metadata_version", metadata_id, expected_version)
            session.expire(row)
            session.refresh(row)
            return self._to_domain(row)

    def get(self, metadata_id: str) -> MetadataVersion:
        with self.session_factory() as session:
            row = session.get(MetadataVersionRow, metadata_id)
            if row is None:
                raise EntityNotFoundError("metadata_version", metadata_id)
            return self._to_domain(row)

    def list_for_package(self, package_id: str) -> list[MetadataVersion]:
        statement: Select[tuple[MetadataVersionRow]] = (
            select(MetadataVersionRow)
            .where(MetadataVersionRow.package_id == package_id)
            .order_by(MetadataVersionRow.version.asc())
        )
        with self.session_factory() as session:
            return [self._to_domain(row) for row in session.scalars(statement)]

    @staticmethod
    def _require_category(session: Session, category: str) -> None:
        row = session.get(MetadataCategoryRow, category)
        if row is None or not row.active:
            raise EntityNotFoundError("metadata_category", category)

    @staticmethod
    def _next_version(session: Session, package_id: str) -> int:
        current = session.scalar(
            select(func.max(MetadataVersionRow.version)).where(
                MetadataVersionRow.package_id == package_id
            )
        )
        return int(current or 0) + 1

    @staticmethod
    def _latest_row(session: Session, package_id: str) -> MetadataVersionRow:
        row = session.scalar(
            select(MetadataVersionRow)
            .where(MetadataVersionRow.package_id == package_id)
            .order_by(MetadataVersionRow.version.desc())
            .limit(1)
        )
        if row is None:
            raise EntityNotFoundError("metadata_for_package", package_id)
        return row

    @staticmethod
    def _new_row(
        *,
        package_id: str,
        analysis_run_id: str,
        version: int,
        content: MetadataContent,
        created_by: str,
        change_note: str | None,
    ) -> MetadataVersionRow:
        return MetadataVersionRow(
            id=str(uuid4()),
            package_id=package_id,
            analysis_run_id=analysis_run_id,
            version=version,
            status=MetadataStatus.DRAFT.value,
            title=content.title,
            description=content.description,
            category=content.category,
            chapters=[chapter.model_dump(mode="json") for chapter in content.chapters],
            narration_language=content.narration_language,
            created_by=created_by,
            change_note=change_note,
        )

    @staticmethod
    def _to_domain(row: MetadataVersionRow) -> MetadataVersion:
        return MetadataVersion(
            id=row.id,
            package_id=row.package_id,
            analysis_run_id=row.analysis_run_id,
            version=row.version,
            status=MetadataStatus(row.status),
            title=row.title,
            description=row.description,
            category=row.category,
            chapters=[MetadataChapter.model_validate(chapter) for chapter in row.chapters],
            narration_language=row.narration_language,
            created_by=row.created_by,
            change_note=row.change_note,
            created_at=row.created_at,
            approved_at=row.approved_at,
        )
