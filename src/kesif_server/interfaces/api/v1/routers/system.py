from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from ..... import __version__
from .....container import Container
from .....domain.entities import User
from ...deps import current_user, get_container

router = APIRouter(tags=["sistem"])


@router.get("/health")
def health(c: Container = Depends(get_container)):
    """Canlılık/hazırlık: veritabanı ve kuyruk erişimi. Kimlik gerektirmez (yük dengeleyici kullanır)."""
    db_ok = True
    try:
        with c.db_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    queue_ok = c.queue.ping()
    return {"ok": db_ok and queue_ok, "version": __version__, "db": db_ok, "queue": queue_ok, "env": c.settings.env,
            "auth_mode": c.settings.auth_mode}


@router.get("/health/services")
def health_services(_: User = Depends(current_user), c: Container = Depends(get_container)):
    """Dış veri kaynaklarının erişilebilirlik ve biçim kontrolü (yavaş; isteğe bağlı)."""
    return c.external.health()


@router.get("/compare")
def compare(_: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.results.compare()


@router.get("/drugs/{chembl_id}/indications")
def drug_indications(chembl_id: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.external.indications(chembl_id)
