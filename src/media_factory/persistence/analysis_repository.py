from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.analysis import (
    AnalysisClip,
    AnalysisRun,
    AnalysisRunState,
    ClipAnalysis,
    ClipInterval,
    ReviewStatus,
    TimelineEvent,
)
from media_factory.domain.errors import EntityNotFoundError
from media_factory.persistence.tables import (
    AnalysisClipRow,
    AnalysisEventRow,
    AnalysisRunRow,
    utc_now,
)


class SQLAlchemyAnalysisRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_run(
        self,
        *,
        package_id: str,
        source_sha256: str,
        provider_name: str,
        provider_version: str,
        prompt_version: str,
        inference_parameters: dict[str, str | int | float | bool | None],
    ) -> AnalysisRun:
        row = AnalysisRunRow(
            id=str(uuid4()),
            package_id=package_id,
            source_sha256=source_sha256,
            state=AnalysisRunState.RUNNING.value,
            provider_name=provider_name,
            provider_version=provider_version,
            prompt_version=prompt_version,
            inference_parameters=inference_parameters,
            processing_manifest={},
        )
        with self.session_factory.begin() as session:
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._run_to_domain(row)

    def set_proxy(
        self,
        run_id: str,
        *,
        proxy_path: Path,
        command: list[str],
    ) -> AnalysisRun:
        return self._update_run(
            run_id,
            proxy_path=str(proxy_path),
            processing_manifest={"proxy_command": command},
        )

    def add_clip(
        self,
        *,
        run_id: str,
        interval: ClipInterval,
        clip_path: Path,
        extraction_command: list[str],
        result: ClipAnalysis,
        inference_seconds: float,
    ) -> AnalysisClip:
        row = AnalysisClipRow(
            id=str(uuid4()),
            analysis_run_id=run_id,
            start_seconds=interval.start,
            end_seconds=interval.end,
            clip_path=str(clip_path),
            extraction_command=extraction_command,
            result=result.model_dump(mode="json"),
            inference_seconds=inference_seconds,
        )
        with self.session_factory.begin() as session:
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._clip_to_domain(row)

    def succeed(self, run_id: str) -> AnalysisRun:
        return self._update_run(
            run_id,
            state=AnalysisRunState.SUCCEEDED.value,
            finished_at=utc_now(),
        )

    def fail(self, run_id: str, *, code: str, message: str) -> AnalysisRun:
        return self._update_run(
            run_id,
            state=AnalysisRunState.FAILED.value,
            error_code=code,
            error_message=message,
            finished_at=utc_now(),
        )

    def get(self, run_id: str) -> AnalysisRun:
        with self.session_factory() as session:
            row = session.get(AnalysisRunRow, run_id)
            if row is None:
                raise EntityNotFoundError("analysis_run", run_id)
            return self._run_to_domain(row)

    def list_clips(self, run_id: str) -> list[AnalysisClip]:
        statement: Select[tuple[AnalysisClipRow]] = (
            select(AnalysisClipRow)
            .where(AnalysisClipRow.analysis_run_id == run_id)
            .order_by(AnalysisClipRow.start_seconds.asc())
        )
        with self.session_factory() as session:
            return [self._clip_to_domain(row) for row in session.scalars(statement)]

    def save_events(self, events: list[TimelineEvent]) -> None:
        with self.session_factory.begin() as session:
            session.add_all(
                [
                    AnalysisEventRow(
                        id=event.id,
                        analysis_run_id=event.analysis_run_id,
                        start_seconds=event.start,
                        end_seconds=event.end,
                        actor=event.actor,
                        action=event.action,
                        objects=event.objects,
                        evidence=event.evidence,
                        confidence=event.confidence,
                        source_clip_ids=event.source_clip_ids,
                        conflict_event_ids=event.conflict_event_ids,
                        review_status=event.review_status.value,
                    )
                    for event in events
                ]
            )

    def list_events(self, run_id: str) -> list[TimelineEvent]:
        statement: Select[tuple[AnalysisEventRow]] = (
            select(AnalysisEventRow)
            .where(AnalysisEventRow.analysis_run_id == run_id)
            .order_by(AnalysisEventRow.start_seconds.asc(), AnalysisEventRow.id.asc())
        )
        with self.session_factory() as session:
            return [self._event_to_domain(row) for row in session.scalars(statement)]

    def _update_run(self, run_id: str, **values: Any) -> AnalysisRun:
        with self.session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(AnalysisRunRow)
                    .where(AnalysisRunRow.id == run_id)
                    .values(**values)
                ),
            )
            if result.rowcount != 1:
                raise EntityNotFoundError("analysis_run", run_id)
            row = session.get(AnalysisRunRow, run_id)
            if row is None:
                raise EntityNotFoundError("analysis_run", run_id)
            return self._run_to_domain(row)

    @staticmethod
    def _run_to_domain(row: AnalysisRunRow) -> AnalysisRun:
        return AnalysisRun(
            id=row.id,
            package_id=row.package_id,
            source_sha256=row.source_sha256,
            state=AnalysisRunState(row.state),
            provider_name=row.provider_name,
            provider_version=row.provider_version,
            prompt_version=row.prompt_version,
            inference_parameters=row.inference_parameters,
            proxy_path=row.proxy_path,
            error_code=row.error_code,
            error_message=row.error_message,
            created_at=row.created_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _clip_to_domain(row: AnalysisClipRow) -> AnalysisClip:
        return AnalysisClip(
            id=row.id,
            analysis_run_id=row.analysis_run_id,
            interval=ClipInterval(start=row.start_seconds, end=row.end_seconds),
            clip_path=row.clip_path,
            result=ClipAnalysis.model_validate(row.result),
            inference_seconds=row.inference_seconds,
            created_at=row.created_at,
        )

    @staticmethod
    def _event_to_domain(row: AnalysisEventRow) -> TimelineEvent:
        return TimelineEvent(
            id=row.id,
            analysis_run_id=row.analysis_run_id,
            start=row.start_seconds,
            end=row.end_seconds,
            actor=row.actor,
            action=row.action,
            objects=row.objects,
            evidence=row.evidence,
            confidence=row.confidence,
            source_clip_ids=row.source_clip_ids,
            conflict_event_ids=row.conflict_event_ids,
            review_status=ReviewStatus(row.review_status),
            created_at=row.created_at,
        )
