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
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
