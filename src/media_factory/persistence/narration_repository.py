from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.errors import EntityNotFoundError, VersionConflictError
from media_factory.domain.narration import (
    AudioDecision,
    AudioPolicy,
    AudioTrack,
    NarrationScript,
    NarrationScriptContent,
    NarrationScriptStatus,
    TTSRun,
    TTSRunState,
)
from media_factory.persistence.tables import (
    AudioDecisionRow,
    AudioTrackRow,
    NarrationScriptRow,
    TTSRunRow,
    utc_now,
)


class SQLAlchemyNarrationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_script(
        self,
        *,
        package_id: str,
        metadata_version_id: str,
        content: NarrationScriptContent,
        created_by: str,
        change_note: str | None,
    ) -> NarrationScript:
        with self.session_factory.begin() as session:
            version = self._next_script_version(session, package_id)
            session.execute(
                update(NarrationScriptRow)
                .where(NarrationScriptRow.package_id == package_id)
                .where(NarrationScriptRow.status == NarrationScriptStatus.DRAFT.value)
                .values(status=NarrationScriptStatus.SUPERSEDED.value)
            )
            row = self._new_script_row(
                package_id=package_id,
                metadata_version_id=metadata_version_id,
                version=version,
                content=content,
                created_by=created_by,
                change_note=change_note,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._script_to_domain(row)

    def revise_script(
        self,
        script_id: str,
        *,
        base_version: int,
        content: NarrationScriptContent,
        created_by: str,
        change_note: str | None,
    ) -> NarrationScript:
        with self.session_factory.begin() as session:
            base = session.get(NarrationScriptRow, script_id)
            if base is None:
                raise EntityNotFoundError("narration_script", script_id)
            latest = self._latest_script_row(session, base.package_id)
            if (
                base.version != base_version
                or latest.id != base.id
                or base.status != NarrationScriptStatus.DRAFT.value
            ):
                raise VersionConflictError("narration_script", script_id, base_version)
            base.status = NarrationScriptStatus.SUPERSEDED.value
            row = self._new_script_row(
                package_id=base.package_id,
                metadata_version_id=base.metadata_version_id,
                version=base.version + 1,
                content=content,
                created_by=created_by,
                change_note=change_note,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._script_to_domain(row)

    def approve_script(self, script_id: str, *, expected_version: int) -> NarrationScript:
        with self.session_factory.begin() as session:
            row = session.get(NarrationScriptRow, script_id)
            if row is None:
                raise EntityNotFoundError("narration_script", script_id)
            latest = self._latest_script_row(session, row.package_id)
            if (
                row.version != expected_version
                or latest.id != row.id
                or row.status != NarrationScriptStatus.DRAFT.value
            ):
                raise VersionConflictError("narration_script", script_id, expected_version)
            row.status = NarrationScriptStatus.APPROVED.value
            row.approved_at = utc_now()
            session.flush()
            session.refresh(row)
            return self._script_to_domain(row)

    def get_script(self, script_id: str) -> NarrationScript:
        with self.session_factory() as session:
            row = session.get(NarrationScriptRow, script_id)
            if row is None:
                raise EntityNotFoundError("narration_script", script_id)
            return self._script_to_domain(row)

    def list_scripts(self, package_id: str) -> list[NarrationScript]:
        statement: Select[tuple[NarrationScriptRow]] = (
            select(NarrationScriptRow)
            .where(NarrationScriptRow.package_id == package_id)
            .order_by(NarrationScriptRow.version.asc())
        )
        with self.session_factory() as session:
            return [self._script_to_domain(row) for row in session.scalars(statement)]

    def create_tts_run(
        self,
        *,
        package_id: str,
        script_id: str,
        provider_name: str,
        provider_version: str,
        parameters: dict[str, str | int | float | bool | None],
    ) -> TTSRun:
        row = TTSRunRow(
            id=str(uuid4()),
            package_id=package_id,
            script_id=script_id,
            state=TTSRunState.RUNNING.value,
            provider_name=provider_name,
            provider_version=provider_version,
            parameters=parameters,
        )
        with self.session_factory.begin() as session:
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._run_to_domain(row)

    def succeed_tts_run(self, run_id: str) -> TTSRun:
        return self._update_run(
            run_id,
            state=TTSRunState.SUCCEEDED.value,
            finished_at=utc_now(),
        )

    def list_tts_runs(self, package_id: str) -> list[TTSRun]:
        statement: Select[tuple[TTSRunRow]] = (
            select(TTSRunRow)
            .where(TTSRunRow.package_id == package_id)
            .order_by(TTSRunRow.created_at.asc())
        )
        with self.session_factory() as session:
            return [self._run_to_domain(row) for row in session.scalars(statement)]

    def fail_tts_run(self, run_id: str, *, code: str, message: str) -> TTSRun:
        return self._update_run(
            run_id,
            state=TTSRunState.FAILED.value,
            error_code=code,
            error_message=message,
            finished_at=utc_now(),
        )

    def add_audio_track(
        self,
        *,
        package_id: str,
        tts_run_id: str,
        script_id: str,
        path: Path,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        duration: float,
        language: str,
        synthetic: bool,
    ) -> AudioTrack:
        row = AudioTrackRow(
            id=str(uuid4()),
            package_id=package_id,
            tts_run_id=tts_run_id,
            script_id=script_id,
            path=str(path),
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            duration=duration,
            language=language,
            synthetic=synthetic,
        )
        with self.session_factory.begin() as session:
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._track_to_domain(row)

    def get_audio_track(self, track_id: str) -> AudioTrack:
        with self.session_factory() as session:
            row = session.get(AudioTrackRow, track_id)
            if row is None:
                raise EntityNotFoundError("audio_track", track_id)
            return self._track_to_domain(row)

    def list_audio_tracks(self, package_id: str) -> list[AudioTrack]:
        statement: Select[tuple[AudioTrackRow]] = (
            select(AudioTrackRow)
            .where(AudioTrackRow.package_id == package_id)
            .order_by(AudioTrackRow.created_at.asc())
        )
        with self.session_factory() as session:
            return [self._track_to_domain(row) for row in session.scalars(statement)]

    def create_audio_decision(
        self,
        *,
        package_id: str,
        policy: AudioPolicy,
        audio_track_id: str | None,
        created_by: str,
    ) -> AudioDecision:
        with self.session_factory.begin() as session:
            current = session.scalar(
                select(func.max(AudioDecisionRow.version)).where(
                    AudioDecisionRow.package_id == package_id
                )
            )
            row = AudioDecisionRow(
                id=str(uuid4()),
                package_id=package_id,
                version=int(current or 0) + 1,
                policy=policy.value,
                audio_track_id=audio_track_id,
                created_by=created_by,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._decision_to_domain(row)

    def list_audio_decisions(self, package_id: str) -> list[AudioDecision]:
        statement: Select[tuple[AudioDecisionRow]] = (
            select(AudioDecisionRow)
            .where(AudioDecisionRow.package_id == package_id)
            .order_by(AudioDecisionRow.version.asc())
        )
        with self.session_factory() as session:
            return [self._decision_to_domain(row) for row in session.scalars(statement)]

    def get_latest_audio_decision(self, package_id: str) -> AudioDecision:
        with self.session_factory() as session:
            row = session.scalar(
                select(AudioDecisionRow)
                .where(AudioDecisionRow.package_id == package_id)
                .order_by(AudioDecisionRow.version.desc())
                .limit(1)
            )
            if row is None:
                raise EntityNotFoundError("audio_decision_for_package", package_id)
            return self._decision_to_domain(row)

    def _update_run(self, run_id: str, **values: Any) -> TTSRun:
        with self.session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(update(TTSRunRow).where(TTSRunRow.id == run_id).values(**values)),
            )
            if result.rowcount != 1:
                raise EntityNotFoundError("tts_run", run_id)
            row = session.get(TTSRunRow, run_id)
            if row is None:
                raise EntityNotFoundError("tts_run", run_id)
            return self._run_to_domain(row)

    @staticmethod
    def _next_script_version(session: Session, package_id: str) -> int:
        current = session.scalar(
            select(func.max(NarrationScriptRow.version)).where(
                NarrationScriptRow.package_id == package_id
            )
        )
        return int(current or 0) + 1

    @staticmethod
    def _latest_script_row(session: Session, package_id: str) -> NarrationScriptRow:
        row = session.scalar(
            select(NarrationScriptRow)
            .where(NarrationScriptRow.package_id == package_id)
            .order_by(NarrationScriptRow.version.desc())
            .limit(1)
        )
        if row is None:
            raise EntityNotFoundError("narration_for_package", package_id)
        return row

    @staticmethod
    def _new_script_row(
        *,
        package_id: str,
        metadata_version_id: str,
        version: int,
        content: NarrationScriptContent,
        created_by: str,
        change_note: str | None,
    ) -> NarrationScriptRow:
        return NarrationScriptRow(
            id=str(uuid4()),
            package_id=package_id,
            metadata_version_id=metadata_version_id,
            version=version,
            status=NarrationScriptStatus.DRAFT.value,
            text=content.text,
            language=content.language,
            style=content.style,
            target_wpm=content.target_wpm,
            created_by=created_by,
            change_note=change_note,
        )

    @staticmethod
    def _script_to_domain(row: NarrationScriptRow) -> NarrationScript:
        return NarrationScript(
            id=row.id,
            package_id=row.package_id,
            metadata_version_id=row.metadata_version_id,
            version=row.version,
            status=NarrationScriptStatus(row.status),
            text=row.text,
            language=row.language,
            style=row.style,
            target_wpm=row.target_wpm,
            created_by=row.created_by,
            change_note=row.change_note,
            created_at=row.created_at,
            approved_at=row.approved_at,
        )

    @staticmethod
    def _run_to_domain(row: TTSRunRow) -> TTSRun:
        return TTSRun(
            id=row.id,
            package_id=row.package_id,
            script_id=row.script_id,
            state=TTSRunState(row.state),
            provider_name=row.provider_name,
            provider_version=row.provider_version,
            parameters=row.parameters,
            error_code=row.error_code,
            error_message=row.error_message,
            created_at=row.created_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _track_to_domain(row: AudioTrackRow) -> AudioTrack:
        return AudioTrack(
            id=row.id,
            package_id=row.package_id,
            tts_run_id=row.tts_run_id,
            script_id=row.script_id,
            path=row.path,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            duration=row.duration,
            language=row.language,
            synthetic=row.synthetic,
            created_at=row.created_at,
        )

    @staticmethod
    def _decision_to_domain(row: AudioDecisionRow) -> AudioDecision:
        return AudioDecision(
            id=row.id,
            package_id=row.package_id,
            version=row.version,
            policy=AudioPolicy(row.policy),
            audio_track_id=row.audio_track_id,
            created_by=row.created_by,
            created_at=row.created_at,
        )
