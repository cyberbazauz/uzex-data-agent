"""add knowledge_chunks for approved reference material

Revision ID: 9f2c4b7d1e33
Revises: cdf1da1dde9f
Create Date: 2026-11-05 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9f2c4b7d1e33"
down_revision: Union[str, None] = "cdf1da1dde9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Knowledge storage only.  It is intentionally separate from deals and
    # must never receive transaction rows, final answers or user chats.
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("script", sa.Text(), nullable=True),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("source_version", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("approval_status", sa.Text(), nullable=False),
        sa.Column("trust_level", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "approval_status IN ('DRAFT','APPROVED','RETIRED')",
            name="ck_knowledge_chunks_approval",
        ),
        sa.CheckConstraint(
            "trust_level IN ('AUTHORITATIVE','CURATED','EXAMPLE')",
            name="ck_knowledge_chunks_trust",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id", "content_hash", name="ux_knowledge_chunks_source_hash"
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_source_type",
        "knowledge_chunks",
        ["source_type"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_chunks_approval",
        "knowledge_chunks",
        ["approval_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_approval", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_source_type", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
