"""Create validated package build records.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "package_builds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("master_build_id", sa.String(length=36), nullable=False),
        sa.Column("transcription_run_id", sa.String(length=36), nullable=False),
        sa.Column("metadata_version_id", sa.String(length=36), nullable=False),
        sa.Column("delivery_id", sa.String(length=200), nullable=False),
        sa.Column("customer", sa.String(length=200), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("base_name", sa.String(length=200), nullable=False),
        sa.Column("output_dir", sa.Text(), nullable=True),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("manifest_path", sa.Text(), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("computed_metadata", sa.JSON(), nullable=True),
        sa.Column("validation_issues", sa.JSON(), nullable=False),
        sa.Column("build_parameters", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["master_build_id"], ["master_builds.id"]),
        sa.ForeignKeyConstraint(["metadata_version_id"], ["metadata_versions.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"]),
        sa.ForeignKeyConstraint(["transcription_run_id"], ["transcription_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_id", "version", name="uq_package_build_version"),
    )
    op.create_index(
        "ix_package_builds_package_id",
        "package_builds",
        ["package_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_package_builds_package_id", table_name="package_builds")
    op.drop_table("package_builds")
