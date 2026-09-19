from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.errors import EntityNotFoundError, VersionConflictError
from media_factory.domain.package_state import PackageState
from media_factory.domain.qa import QADecision, QAReview
from media_factory.persistence.tables import PackageBuildRow, PackageRow, QAReviewRow, utc_now


class QAReviewAlreadyExists(RuntimeError):
    code = "qa_review_already_exists"


class SQLAlchemyQARepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_and_finalize(
        self,
        *,
        package_id: str,
        package_build_id: str,
        decision: QADecision,
        reviewer: str,
        reason: str | None,
        manifest_sha256: str,
        reviewed_at: datetime,
        expected_package_version: int,
        target_state: PackageState,
    ) -> QAReview:
        row = QAReviewRow(
            id=str(uuid4()),
            package_id=package_id,
            package_build_id=package_build_id,
            decision=decision.value,
            reviewer=reviewer,
            reason=reason,
            manifest_sha256=manifest_sha256,
            reviewed_at=reviewed_at,
        )
        try:
            with self.session_factory.begin() as session:
                session.add(row)
                session.flush()
                package_result = cast(
                    CursorResult[Any],
                    session.execute(
                        update(PackageRow)
                        .where(PackageRow.id == package_id)
                        .where(PackageRow.version == expected_package_version)
                        .values(
                            state=target_state.value,
                            version=PackageRow.version + 1,
                            updated_at=utc_now(),
                        )
                    ),
                )
                if package_result.rowcount != 1:
                    if session.get(PackageRow, package_id) is None:
                        raise EntityNotFoundError("package", package_id)
                    raise VersionConflictError(
                        "package", package_id, expected_package_version
                    )
                build_result = cast(
                    CursorResult[Any],
                    session.execute(
                        update(PackageBuildRow)
                        .where(PackageBuildRow.id == package_build_id)
                        .values(manifest_sha256=manifest_sha256)
                    ),
                )
                if build_result.rowcount != 1:
                    raise EntityNotFoundError("package_build", package_build_id)
                session.refresh(row)
                return self._to_domain(row)
        except IntegrityError as exc:
            raise QAReviewAlreadyExists(
                f"package build {package_build_id} already has a QA decision"
            ) from exc

    def get_for_build(self, package_build_id: str) -> QAReview:
        statement: Select[tuple[QAReviewRow]] = select(QAReviewRow).where(
            QAReviewRow.package_build_id == package_build_id
        )
        with self.session_factory() as session:
            row = session.scalar(statement)
            if row is None:
                raise EntityNotFoundError("qa_review_for_build", package_build_id)
            return self._to_domain(row)

    def list_for_package(self, package_id: str) -> list[QAReview]:
        statement: Select[tuple[QAReviewRow]] = (
            select(QAReviewRow)
            .where(QAReviewRow.package_id == package_id)
            .order_by(QAReviewRow.reviewed_at.asc())
        )
        with self.session_factory() as session:
            return [self._to_domain(row) for row in session.scalars(statement)]

    @staticmethod
    def _to_domain(row: QAReviewRow) -> QAReview:
        return QAReview(
            id=row.id,
            package_id=row.package_id,
            package_build_id=row.package_build_id,
            decision=QADecision(row.decision),
            reviewer=row.reviewer,
            reason=row.reason,
            manifest_sha256=row.manifest_sha256,
            reviewed_at=row.reviewed_at,
        )
