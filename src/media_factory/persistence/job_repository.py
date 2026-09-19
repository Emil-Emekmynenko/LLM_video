from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.errors import EntityNotFoundError, IdempotencyConflictError
from media_factory.domain.job import Job, JobKind, JobState
from media_factory.persistence.tables import JobRow, PackageRow, utc_now


class SQLAlchemyJobRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_or_get(
        self,
        *,
        package_id: str,
        kind: JobKind,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> tuple[Job, bool]:
        with self.session_factory() as session:
            if session.get(PackageRow, package_id) is None:
                raise EntityNotFoundError("package", package_id)
            existing = session.scalar(
                select(JobRow)
                .where(JobRow.package_id == package_id)
                .where(JobRow.idempotency_key == idempotency_key)
            )
            if existing is not None:
                self._verify_same_request(existing, kind=kind, payload=payload)
                return self._to_domain(existing), False

            row = JobRow(
                id=str(uuid4()),
                package_id=package_id,
                kind=kind.value,
                state=JobState.QUEUED.value,
                idempotency_key=idempotency_key,
                payload=payload,
                progress=0,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raced = session.scalar(
                    select(JobRow)
                    .where(JobRow.package_id == package_id)
                    .where(JobRow.idempotency_key == idempotency_key)
                )
                if raced is None:
                    raise
                self._verify_same_request(raced, kind=kind, payload=payload)
                return self._to_domain(raced), False
            session.refresh(row)
            return self._to_domain(row), True

    def get(self, job_id: str) -> Job:
        with self.session_factory() as session:
            row = session.get(JobRow, job_id)
            if row is None:
                raise EntityNotFoundError("job", job_id)
            return self._to_domain(row)

    def list_for_package(self, package_id: str) -> list[Job]:
        statement: Select[tuple[JobRow]] = (
            select(JobRow)
            .where(JobRow.package_id == package_id)
            .order_by(JobRow.created_at.desc())
        )
        with self.session_factory() as session:
            return [self._to_domain(row) for row in session.scalars(statement)]

    def mark_dispatched(self, job_id: str) -> Job:
        return self._update(job_id, dispatched_at=utc_now())

    def mark_running(self, job_id: str) -> Job:
        return self._update(
            job_id,
            state=JobState.RUNNING.value,
            started_at=utc_now(),
            progress=1,
        )

    def mark_succeeded(self, job_id: str) -> Job:
        return self._update(
            job_id,
            state=JobState.SUCCEEDED.value,
            finished_at=utc_now(),
            progress=100,
        )

    def mark_failed(self, job_id: str, *, code: str, message: str) -> Job:
        return self._update(
            job_id,
            state=JobState.FAILED.value,
            finished_at=utc_now(),
            error_code=code,
            error_message=message,
        )

    def _update(self, job_id: str, **values: str | int | datetime | None) -> Job:
        values["updated_at"] = utc_now()
        with self.session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(update(JobRow).where(JobRow.id == job_id).values(**values)),
            )
            if result.rowcount != 1:
                raise EntityNotFoundError("job", job_id)
            row = session.get(JobRow, job_id)
            if row is None:
                raise EntityNotFoundError("job", job_id)
            return self._to_domain(row)

    @staticmethod
    def _verify_same_request(
        row: JobRow,
        *,
        kind: JobKind,
        payload: dict[str, Any],
    ) -> None:
        if row.kind != kind.value or row.payload != payload:
            raise IdempotencyConflictError(row.idempotency_key)

    @staticmethod
    def _to_domain(row: JobRow) -> Job:
        return Job(
            id=row.id,
            package_id=row.package_id,
            kind=JobKind(row.kind),
            state=JobState(row.state),
            idempotency_key=row.idempotency_key,
            payload=row.payload,
            progress=row.progress,
            error_code=row.error_code,
            error_message=row.error_message,
            dispatched_at=row.dispatched_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
