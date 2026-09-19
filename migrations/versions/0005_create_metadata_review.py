"""Create analysis review and metadata versions.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIES = (
    ("Repairs_And_DIY", "Repairs and DIY"),
    ("Cooking", "Cooking"),
    ("Arts_And_Crafts", "Arts and Crafts"),
    ("Technology", "Technology"),
    ("Nature", "Nature"),
    ("Sports_And_Fitness", "Sports and Fitness"),
    ("Other", "Other"),
)


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column(
            "review_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "analysis_events",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "analysis_events",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "metadata_categories",
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    category_table = sa.table(
        "metadata_categories",
        sa.column("code", sa.String()),
        sa.column("label", sa.String()),
        sa.column("active", sa.Boolean()),
    )
    op.bulk_insert(
        category_table,
        [{"code": code, "label": label, "active": True} for code, label in CATEGORIES],
    )
    op.create_table(
        "metadata_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("chapters", sa.JSON(), nullable=False),
        sa.Column("narration_language", sa.String(length=35), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.ForeignKeyConstraint(["category"], ["metadata_categories.code"]),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_id", "version", name="uq_metadata_package_version"),
    )
    op.create_index(
        "ix_metadata_versions_package_id",
        "metadata_versions",
        ["package_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_metadata_versions_package_id", table_name="metadata_versions")
    op.drop_table("metadata_versions")
    op.drop_table("metadata_categories")
    op.drop_column("analysis_events", "updated_at")
    op.drop_column("analysis_events", "version")
    op.drop_column("analysis_runs", "reviewed_at")
    op.drop_column("analysis_runs", "review_status")
