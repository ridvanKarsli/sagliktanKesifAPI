"""Ürün adı "Sağlıktan Keşif" oldu: sistem kullanıcısının iç e-postası yeni ada taşınır.

Revision ID: 0004_rename_system_user
Revises: 0003_engine_files
Create Date: 2026-10-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_rename_system_user"
down_revision = "0003_engine_files"
branch_labels = None
depends_on = None

OLD, NEW = "local@ilackesif.local", "system@kesif.local"


def _move(src: str, dst: str) -> None:
    users = sa.table("users", sa.column("email", sa.String))
    conn = op.get_bind()
    exists = conn.execute(sa.select(sa.func.count()).select_from(users).where(users.c.email == dst)).scalar()
    if not exists:
        conn.execute(users.update().where(users.c.email == src).values(email=dst))


def upgrade() -> None:
    _move(OLD, NEW)


def downgrade() -> None:
    _move(NEW, OLD)
