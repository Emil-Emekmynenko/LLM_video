"""Create persistent upload checkpoints.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "upload_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("delivery_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("remote_key", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("encrypted_session_token", sa.Text(), nullable=False),
        sa.Column("next_offset", sa.BigInteger(), nullable=False),
        sa.Column("completed_parts", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["delivery_attempt_id"], ["delivery_attempts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "delivery_attempt_id", "remote_key", name="uq_upload_checkpoint_remote_key"
        ),
    )
    op.create_index(
        "ix_upload_checkpoints_delivery_attempt_id",
        "upload_checkpoints",
        ["delivery_attempt_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_upload_checkpoints_delivery_attempt_id", table_name="upload_checkpoints"
    )
    op.drop_table("upload_checkpoints")
