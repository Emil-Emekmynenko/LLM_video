"""Create merged analysis events and inference metadata.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column(
            "inference_parameters",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "analysis_clips",
        sa.Column("inference_seconds", sa.Float(), nullable=True),
    )
    op.create_table(
        "analysis_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=36), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("objects", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_clip_ids", sa.JSON(), nullable=False),
        sa.Column("conflict_event_ids", sa.JSON(), nullable=False),
        sa.Column("review_status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_events_run_id",
        "analysis_events",
        ["analysis_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_events_run_id", table_name="analysis_events")
    op.drop_table("analysis_events")
    op.drop_column("analysis_clips", "inference_seconds")
    op.drop_column("analysis_runs", "inference_parameters")
