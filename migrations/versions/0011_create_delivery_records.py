"""Create delivery attempts and uploaded object records.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("package_build_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("media_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sidecars_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("package_complete_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["package_build_id"], ["package_builds.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "package_id", "idempotency_key", name="uq_delivery_attempt_idempotency"
        ),
        sa.UniqueConstraint("package_build_id", name="uq_delivery_attempt_package_build"),
    )
    op.create_index(
        "ix_delivery_attempts_package_id", "delivery_attempts", ["package_id"], unique=False
    )
    op.create_table(
        "uploaded_objects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("delivery_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("local_relative_path", sa.Text(), nullable=False),
        sa.Column("remote_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("provider_checksum", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["delivery_attempt_id"], ["delivery_attempts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "delivery_attempt_id", "remote_key", name="uq_uploaded_object_remote_key"
        ),
    )
    op.create_index(
        "ix_uploaded_objects_delivery_attempt_id",
        "uploaded_objects",
        ["delivery_attempt_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_uploaded_objects_delivery_attempt_id", table_name="uploaded_objects"
    )
    op.drop_table("uploaded_objects")
    op.drop_index("ix_delivery_attempts_package_id", table_name="delivery_attempts")
    op.drop_table("delivery_attempts")
