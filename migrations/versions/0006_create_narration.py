"""Create narration, TTS, and audio policy tables.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "narration_scripts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("metadata_version_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=False),
        sa.Column("style", sa.String(length=200), nullable=False),
        sa.Column("target_wpm", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["metadata_version_id"], ["metadata_versions.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_id", "version", name="uq_narration_package_version"),
    )
    op.create_index(
        "ix_narration_scripts_package_id",
        "narration_scripts",
        ["package_id"],
        unique=False,
    )
    op.create_table(
        "tts_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("script_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("provider_name", sa.String(length=120), nullable=False),
        sa.Column("provider_version", sa.String(length=80), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"]),
        sa.ForeignKeyConstraint(["script_id"], ["narration_scripts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tts_runs_package_id", "tts_runs", ["package_id"], unique=False)
    op.create_table(
        "audio_tracks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("tts_run_id", sa.String(length=36), nullable=False),
        sa.Column("script_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"]),
        sa.ForeignKeyConstraint(["script_id"], ["narration_scripts.id"]),
        sa.ForeignKeyConstraint(["tts_run_id"], ["tts_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audio_tracks_package_id", "audio_tracks", ["package_id"], unique=False
    )
    op.create_table(
        "audio_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("policy", sa.String(length=20), nullable=False),
        sa.Column("audio_track_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["audio_track_id"], ["audio_tracks.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_id", "version", name="uq_audio_decision_version"),
    )
    op.create_index(
        "ix_audio_decisions_package_id",
        "audio_decisions",
        ["package_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audio_decisions_package_id", table_name="audio_decisions")
    op.drop_table("audio_decisions")
    op.drop_index("ix_audio_tracks_package_id", table_name="audio_tracks")
    op.drop_table("audio_tracks")
    op.drop_index("ix_tts_runs_package_id", table_name="tts_runs")
    op.drop_table("tts_runs")
    op.drop_index("ix_narration_scripts_package_id", table_name="narration_scripts")
    op.drop_table("narration_scripts")
