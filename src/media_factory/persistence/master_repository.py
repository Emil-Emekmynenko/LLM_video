from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from media_factory.domain.errors import EntityNotFoundError
from media_factory.domain.master import MasterBuild, MasterBuildState
from media_factory.domain.models import MediaInspection
from media_factory.persistence.tables import MasterBuildRow, utc_now


class SQLAlchemyMasterRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(
        self,
        *,
        package_id: str,
        source_asset_id: str,
        source_sha256: str,
        audio_decision_id: str,
    ) -> MasterBuild:
        row = MasterBuildRow(
            id=str(uuid4()),
            package_id=package_id,
            source_asset_id=source_asset_id,
            source_sha256=source_sha256,
            audio_decision_id=audio_decision_id,
            state=MasterBuildState.RUNNING.value,
            ffmpeg_command=[],
            decode_command=[],
            video_stream_copy=True,
        )
        with self.session_factory.begin() as session:
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._to_domain(row)

    def succeed(
        self,
        build_id: str,
        *,
        output_path: Path,
        output_size_bytes: int,
        output_sha256: str,
        inspection: MediaInspection,
        ffmpeg_command: list[str],
        decode_command: list[str],
    ) -> MasterBuild:
        return self._update(
            build_id,
            state=MasterBuildState.SUCCEEDED.value,
            output_path=str(output_path),
            output_size_bytes=output_size_bytes,
            output_sha256=output_sha256,
            inspection=inspection.model_dump(mode="json"),
            ffmpeg_command=ffmpeg_command,
            decode_command=decode_command,
            video_stream_copy=True,
            finished_at=utc_now(),
        )

    def fail(
        self,
        build_id: str,
        *,
        code: str,
        message: str,
        ffmpeg_command: list[str],
    ) -> MasterBuild:
        return self._update(
            build_id,
            state=MasterBuildState.FAILED.value,
            error_code=code,
            error_message=message,
            ffmpeg_command=ffmpeg_command,
            finished_at=utc_now(),
        )

    def get(self, build_id: str) -> MasterBuild:
        with self.session_factory() as session:
            row = session.get(MasterBuildRow, build_id)
            if row is None:
                raise EntityNotFoundError("master_build", build_id)
            return self._to_domain(row)

    def list_for_package(self, package_id: str) -> list[MasterBuild]:
        statement: Select[tuple[MasterBuildRow]] = (
            select(MasterBuildRow)
            .where(MasterBuildRow.package_id == package_id)
            .order_by(MasterBuildRow.created_at.asc())
        )
        with self.session_factory() as session:
            return [self._to_domain(row) for row in session.scalars(statement)]

    def _update(self, build_id: str, **values: Any) -> MasterBuild:
        with self.session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(MasterBuildRow)
                    .where(MasterBuildRow.id == build_id)
                    .values(**values)
                ),
            )
            if result.rowcount != 1:
                raise EntityNotFoundError("master_build", build_id)
            row = session.get(MasterBuildRow, build_id)
            if row is None:
                raise EntityNotFoundError("master_build", build_id)
            return self._to_domain(row)

    @staticmethod
    def _to_domain(row: MasterBuildRow) -> MasterBuild:
        return MasterBuild(
            id=row.id,
            package_id=row.package_id,
            source_asset_id=row.source_asset_id,
            source_sha256=row.source_sha256,
            audio_decision_id=row.audio_decision_id,
            state=MasterBuildState(row.state),
            output_path=row.output_path,
            output_size_bytes=row.output_size_bytes,
            output_sha256=row.output_sha256,
            inspection=(
                MediaInspection.model_validate(row.inspection)
                if row.inspection is not None
                else None
            ),
            ffmpeg_command=row.ffmpeg_command,
            decode_command=row.decode_command,
            video_stream_copy=row.video_stream_copy,
            error_code=row.error_code,
            error_message=row.error_message,
            created_at=row.created_at,
            finished_at=row.finished_at,
        )
