"""Motor çalışma dosyaları veritabanında (engine_files): diskte kalıcı veri kalmaz.

Revision ID: 0003_engine_files
Revises: 0002_results_in_db
Create Date: 2026-10-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_engine_files"
down_revision = "0002_results_in_db"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "engine_files",
        sa.Column("path", sa.String(400), primary_key=True),
        sa.Column("scope", sa.String(120), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_engine_files_scope", "engine_files", ["scope"])


def downgrade() -> None:
    op.drop_index("ix_engine_files_scope", table_name="engine_files")
    op.drop_table("engine_files")
