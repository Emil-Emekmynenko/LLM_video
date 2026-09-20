from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.errors import EntityNotFoundError
from media_factory.domain.models import Transcript, ValidationIssue
from media_factory.domain.transcription import TranscriptionRun, TranscriptionRunState
from media_factory.persistence.tables import TranscriptionRunRow, utc_now


class SQLAlchemyTranscriptionRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(
        self,
        *,
        package_id: str,
        master_build_id: str,
        master_sha256: str,
        provider_name: str,
        provider_version: str,
        model_name: str,
        model_revision: str,
        parameters: dict[str, Any],
    ) -> TranscriptionRun:
        row = TranscriptionRunRow(
            id=str(uuid4()),
            package_id=package_id,
            master_build_id=master_build_id,
            master_sha256=master_sha256,
            state=TranscriptionRunState.TRANSCRIBING.value,
            provider_name=provider_name,
            provider_version=provider_version,
            model_name=model_name,
            model_revision=model_revision,
            parameters=parameters,
            validation_issues=[],
        )
        with self.session_factory.begin() as session:
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._to_domain(row)

    def save_asr_result(
        self,
        run_id: str,
        *,
        transcript: Transcript,
        language_probability: float | None,
        text: str,
    ) -> TranscriptionRun:
        return self._update(
            run_id,
            state=TranscriptionRunState.ALIGNING.value,
            language=transcript.language,
            language_probability=language_probability,
            text=text,
            transcript=transcript.model_dump(mode="json"),
        )

    def succeed(self, run_id: str) -> TranscriptionRun:
        return self._update(
            run_id,
            state=TranscriptionRunState.SUCCEEDED.value,
            validation_issues=[],
            finished_at=utc_now(),
        )

    def fail(
        self,
        run_id: str,
        *,
        code: str,
        message: str,
        issues: list[ValidationIssue] | None = None,
    ) -> TranscriptionRun:
        return self._update(
            run_id,
            state=TranscriptionRunState.FAILED.value,
            error_code=code,
            error_message=message,
            validation_issues=[issue.model_dump(mode="json") for issue in issues or []],
            finished_at=utc_now(),
        )

    def get(self, run_id: str) -> TranscriptionRun:
        with self.session_factory() as session:
            row = session.get(TranscriptionRunRow, run_id)
            if row is None:
                raise EntityNotFoundError("transcription_run", run_id)
            return self._to_domain(row)

    def list_for_package(self, package_id: str) -> list[TranscriptionRun]:
        statement: Select[tuple[TranscriptionRunRow]] = (
            select(TranscriptionRunRow)
            .where(TranscriptionRunRow.package_id == package_id)
            .order_by(TranscriptionRunRow.created_at.asc())
        )
        with self.session_factory() as session:
            return [self._to_domain(row) for row in session.scalars(statement)]

    def get_latest_succeeded(self, package_id: str) -> TranscriptionRun:
        statement: Select[tuple[TranscriptionRunRow]] = (
            select(TranscriptionRunRow)
            .where(TranscriptionRunRow.package_id == package_id)
            .where(TranscriptionRunRow.state == TranscriptionRunState.SUCCEEDED.value)
            .order_by(TranscriptionRunRow.created_at.desc())
            .limit(1)
        )
        with self.session_factory() as session:
            row = session.scalar(statement)
            if row is None:
                raise EntityNotFoundError("successful_transcription", package_id)
            return self._to_domain(row)

    def _update(self, run_id: str, **values: Any) -> TranscriptionRun:
        with self.session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(TranscriptionRunRow)
                    .where(TranscriptionRunRow.id == run_id)
                    .values(**values)
                ),
            )
            if result.rowcount != 1:
                raise EntityNotFoundError("transcription_run", run_id)
            row = session.get(TranscriptionRunRow, run_id)
            if row is None:
                raise EntityNotFoundError("transcription_run", run_id)
            return self._to_domain(row)

    @staticmethod
    def _to_domain(row: TranscriptionRunRow) -> TranscriptionRun:
        return TranscriptionRun(
            id=row.id,
            package_id=row.package_id,
            master_build_id=row.master_build_id,
            master_sha256=row.master_sha256,
            state=TranscriptionRunState(row.state),
            provider_name=row.provider_name,
            provider_version=row.provider_version,
            model_name=row.model_name,
            model_revision=row.model_revision,
            parameters=row.parameters,
            language=row.language,
            language_probability=row.language_probability,
            text=row.text,
            transcript=(
                Transcript.model_validate(row.transcript) if row.transcript is not None else None
            ),
            validation_issues=[
                ValidationIssue.model_validate(issue) for issue in row.validation_issues
            ],
            error_code=row.error_code,
            error_message=row.error_message,
            created_at=row.created_at,
            finished_at=row.finished_at,
        )
