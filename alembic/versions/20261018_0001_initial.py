"""İlk şema: kullanıcılar, hastalıklar, analiz işleri, adaylar.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("password_hash", sa.String(300)),
        sa.Column("external_subject", sa.String(300)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("external_subject", name="uq_users_external_subject"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "diseases",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("name_en", sa.String(300), nullable=False),
        sa.Column("mesh_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("pubmed_query", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_removed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_diseases_is_removed", "diseases", ["is_removed"])

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("disease_key", sa.String(120), sa.ForeignKey("diseases.key", ondelete="CASCADE"), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("summary", sa.JSON()),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_analysis_runs_status", "analysis_runs", ["status"])
    op.create_index("ix_runs_disease_status", "analysis_runs", ["disease_key", "status"])
    op.create_index("ix_runs_created", "analysis_runs", ["created_at"])

    op.create_table(
        "candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("disease_key", sa.String(120), sa.ForeignKey("diseases.key", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(32)),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("chemical_id", sa.String(64), nullable=False),
        sa.Column("chemical", sa.String(300), nullable=False),
        sa.Column("chembl_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("lit_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mech_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("penalty", sa.Float(), nullable=False, server_default="1"),
        sa.Column("direction", sa.String(40), nullable=False, server_default=""),
        sa.Column("mechanism", sa.Text(), nullable=False, server_default=""),
        sa.Column("max_phase", sa.Float()),
        sa.Column("n_direct_a", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_bridges", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_bridges", sa.Text(), nullable=False, server_default=""),
        sa.Column("aliases", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(30), nullable=False, server_default="ilaç"),
        sa.Column("status", sa.String(40), nullable=False, server_default="yeni"),
        sa.Column("harm_signal", sa.String(30), nullable=False, server_default="yok"),
        sa.Column("durum", sa.String(60), nullable=False, server_default=""),
        sa.Column("clinical_trials", sa.Integer()),
        sa.Column("pubmed_direct", sa.Integer()),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("disease_key", "chemical_id", name="uq_candidate_disease_chemical"),
    )
    op.create_index("ix_candidates_disease_rank", "candidates", ["disease_key", "rank"])
    op.create_index("ix_candidates_disease_score", "candidates", ["disease_key", "score"])
    op.create_index("ix_candidates_disease_kind_status", "candidates", ["disease_key", "kind", "status"])


def downgrade() -> None:
    op.drop_table("candidates")
    op.drop_table("analysis_runs")
    op.drop_table("diseases")
    op.drop_table("users")
