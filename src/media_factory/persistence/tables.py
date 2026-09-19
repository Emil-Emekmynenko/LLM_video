from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from media_factory.persistence.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class AssetRow(Base):
    __tablename__ = "assets"
    __table_args__ = (Index("ix_assets_sha256", "sha256"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    duplicate_of: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("assets.id"),
        nullable=True,
    )
    inspection: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class PackageRow(Base):
    __tablename__ = "packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("assets.id"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint(
            "package_id",
            "idempotency_key",
            name="uq_jobs_package_id_idempotency_key",
        ),
        Index("ix_jobs_package_id", "package_id"),
        Index("ix_jobs_state", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    package_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("packages.id"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    progress: Mapped[int] = mapped_column(default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class AnalysisRunRow(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (Index("ix_analysis_runs_package_id", "package_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    package_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("packages.id"),
        nullable=False,
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    inference_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    proxy_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalysisClipRow(Base):
    __tablename__ = "analysis_clips"
    __table_args__ = (Index("ix_analysis_clips_run_id", "analysis_run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("analysis_runs.id"),
        nullable=False,
    )
    start_seconds: Mapped[float] = mapped_column(nullable=False)
    end_seconds: Mapped[float] = mapped_column(nullable=False)
    clip_path: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_command: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    inference_seconds: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class AnalysisEventRow(Base):
    __tablename__ = "analysis_events"
    __table_args__ = (Index("ix_analysis_events_run_id", "analysis_run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("analysis_runs.id"),
        nullable=False,
    )
    start_seconds: Mapped[float] = mapped_column(nullable=False)
    end_seconds: Mapped[float] = mapped_column(nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    objects: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evidence: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    source_clip_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    conflict_event_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MetadataCategoryRow(Base):
    __tablename__ = "metadata_categories"

    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


class MetadataVersionRow(Base):
    __tablename__ = "metadata_versions"
    __table_args__ = (
        UniqueConstraint("package_id", "version", name="uq_metadata_package_version"),
        Index("ix_metadata_versions_package_id", "package_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    package_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("packages.id"), nullable=False
    )
    analysis_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analysis_runs.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(
        String(100), ForeignKey("metadata_categories.code"), nullable=False
    )
    chapters: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    narration_language: Mapped[str | None] = mapped_column(String(35), nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NarrationScriptRow(Base):
    __tablename__ = "narration_scripts"
    __table_args__ = (
        UniqueConstraint("package_id", "version", name="uq_narration_package_version"),
        Index("ix_narration_scripts_package_id", "package_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    package_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("packages.id"), nullable=False
    )
    metadata_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("metadata_versions.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(35), nullable=False)
    style: Mapped[str] = mapped_column(String(200), nullable=False)
    target_wpm: Mapped[int] = mapped_column(nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TTSRunRow(Base):
    __tablename__ = "tts_runs"
    __table_args__ = (Index("ix_tts_runs_package_id", "package_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    package_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("packages.id"), nullable=False
    )
    script_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("narration_scripts.id"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(80), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AudioTrackRow(Base):
    __tablename__ = "audio_tracks"
    __table_args__ = (Index("ix_audio_tracks_package_id", "package_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    package_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("packages.id"), nullable=False
    )
    tts_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tts_runs.id"), nullable=False
    )
    script_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("narration_scripts.id"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    duration: Mapped[float] = mapped_column(nullable=False)
    language: Mapped[str] = mapped_column(String(35), nullable=False)
    synthetic: Mapped[bool] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AudioDecisionRow(Base):
    __tablename__ = "audio_decisions"
    __table_args__ = (
        UniqueConstraint("package_id", "version", name="uq_audio_decision_version"),
        Index("ix_audio_decisions_package_id", "package_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    package_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("packages.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)
    policy: Mapped[str] = mapped_column(String(20), nullable=False)
    audio_track_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("audio_tracks.id"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class MasterBuildRow(Base):
    __tablename__ = "master_builds"
    __table_args__ = (Index("ix_master_builds_package_id", "package_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    package_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("packages.id"), nullable=False
    )
    source_asset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assets.id"), nullable=False
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    audio_decision_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audio_decisions.id"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inspection: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ffmpeg_command: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    decode_command: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    video_stream_copy: Mapped[bool] = mapped_column(nullable=False, default=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TranscriptionRunRow(Base):
    __tablename__ = "transcription_runs"
    __table_args__ = (Index("ix_transcription_runs_package_id", "package_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    package_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("packages.id"), nullable=False
    )
    master_build_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("master_builds.id"), nullable=False
    )
    master_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    language: Mapped[str | None] = mapped_column(String(35), nullable=True)
    language_probability: Mapped[float | None] = mapped_column(nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation_issues: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PackageBuildRow(Base):
    __tablename__ = "package_builds"
    __table_args__ = (
        UniqueConstraint("package_id", "version", name="uq_package_build_version"),
        Index("ix_package_builds_package_id", "package_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    package_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("packages.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    master_build_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("master_builds.id"), nullable=False
    )
    transcription_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transcription_runs.id"), nullable=False
    )
    metadata_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("metadata_versions.id"), nullable=False
    )
    delivery_id: Mapped[str] = mapped_column(String(200), nullable=False)
    customer: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    base_name: Mapped[str] = mapped_column(String(200), nullable=False)
    output_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    manifest_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    computed_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation_issues: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    build_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
