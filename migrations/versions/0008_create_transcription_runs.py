"""Create transcription run records.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transcription_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("master_build_id", sa.String(length=36), nullable=False),
        sa.Column("master_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("provider_name", sa.String(length=120), nullable=False),
        sa.Column("provider_version", sa.String(length=80), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("model_revision", sa.String(length=120), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=True),
        sa.Column("language_probability", sa.Float(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("transcript", sa.JSON(), nullable=True),
        sa.Column("validation_issues", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["master_build_id"], ["master_builds.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transcription_runs_package_id",
        "transcription_runs",
        ["package_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_transcription_runs_package_id", table_name="transcription_runs")
    op.drop_table("transcription_runs")
