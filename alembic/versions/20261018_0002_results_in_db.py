"""Sonuçlar veritabanına: hastalık parametreleri, iş günlüğü, analiz çıktıları (kanıt, köprü, rapor…).

Revision ID: 0002_results_in_db
Revises: 0001_initial
Create Date: 2026-10-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_results_in_db"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("diseases") as b:
        b.add_column(sa.Column("params", sa.JSON(), nullable=True))
    with op.batch_alter_table("analysis_runs") as b:
        b.add_column(sa.Column("log", sa.Text(), nullable=True))
    op.create_table(
        "analysis_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("disease_key", sa.String(120), sa.ForeignKey("diseases.key", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("run_id", sa.String(32)),
        sa.Column("payload", sa.JSON()),
        sa.Column("text", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("disease_key", "kind", name="uq_artifact_disease_kind"),
    )


def downgrade() -> None:
    op.drop_table("analysis_artifacts")
    with op.batch_alter_table("analysis_runs") as b:
        b.drop_column("log")
    with op.batch_alter_table("diseases") as b:
        b.drop_column("params")
