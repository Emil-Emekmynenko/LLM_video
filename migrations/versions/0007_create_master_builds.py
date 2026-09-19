"""Create master build records.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "master_builds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("source_asset_id", sa.String(length=36), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("audio_decision_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("output_path", sa.Text(), nullable=True),
        sa.Column("output_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("inspection", sa.JSON(), nullable=True),
        sa.Column("ffmpeg_command", sa.JSON(), nullable=False),
        sa.Column("decode_command", sa.JSON(), nullable=False),
        sa.Column("video_stream_copy", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["audio_decision_id"], ["audio_decisions.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"]),
        sa.ForeignKeyConstraint(["source_asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_master_builds_package_id",
        "master_builds",
        ["package_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_master_builds_package_id", table_name="master_builds")
    op.drop_table("master_builds")
