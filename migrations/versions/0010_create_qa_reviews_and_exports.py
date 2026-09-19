"""Create QA decisions and local export records.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qa_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("package_build_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("reviewer", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["package_build_id"], ["package_builds.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_build_id", name="uq_qa_review_package_build"),
    )
    op.create_index("ix_qa_reviews_package_id", "qa_reviews", ["package_id"], unique=False)
    op.create_table(
        "local_exports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("package_build_id", sa.String(length=36), nullable=False),
        sa.Column("qa_review_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("archive_path", sa.Text(), nullable=True),
        sa.Column("archive_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("archive_sha256", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["package_build_id"], ["package_builds.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"]),
        sa.ForeignKeyConstraint(["qa_review_id"], ["qa_reviews.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_local_exports_package_build_id",
        "local_exports",
        ["package_build_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_local_exports_package_build_id", table_name="local_exports")
    op.drop_table("local_exports")
    op.drop_index("ix_qa_reviews_package_id", table_name="qa_reviews")
    op.drop_table("qa_reviews")
