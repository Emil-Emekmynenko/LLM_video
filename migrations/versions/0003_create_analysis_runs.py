"""Create analysis runs and clips.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("provider_name", sa.String(length=120), nullable=False),
        sa.Column("provider_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("proxy_path", sa.Text(), nullable=True),
        sa.Column("processing_manifest", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_runs_package_id",
        "analysis_runs",
        ["package_id"],
        unique=False,
    )
    op.create_table(
        "analysis_clips",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=36), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("clip_path", sa.Text(), nullable=False),
        sa.Column("extraction_command", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_clips_run_id",
        "analysis_clips",
        ["analysis_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_clips_run_id", table_name="analysis_clips")
    op.drop_table("analysis_clips")
    op.drop_index("ix_analysis_runs_package_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")

