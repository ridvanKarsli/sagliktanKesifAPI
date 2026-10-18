"""Hastalık sözlüğü (MeSH) senaryoları ve dış servis sorguları."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List

from ...domain.entities import Role, User
from ...domain.errors import ConflictError, ExternalServiceError, ValidationError
from ...domain.ports import DiseaseCatalog, ExternalServices, UnitOfWorkFactory, WorkspaceSync
from ..authorization import require
from .diseases import _get_or_404


class CatalogBuildState:
    """Sözlük kurulumunun süreç içi durumu. Kurulum dakikalar sürer ve nadirdir; kuyruk yerine iş parçacığı yeterlidir."""

    def __init__(self):
        self._lock = threading.Lock()
        self.status = "idle"
        self.stage = ""
        self.error = None
        self.started_at = None

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "stage": self.stage, "error": self.error, "started_at": self.started_at}


class CatalogService:
    def __init__(self, uow_factory: UnitOfWorkFactory, catalog: DiseaseCatalog, state: CatalogBuildState, workspace: WorkspaceSync):
        self._uow, self._catalog, self._state, self._ws = uow_factory, catalog, state, workspace

    def status(self) -> Dict[str, Any]:
        return {**self._catalog.status(), "build": self._state.to_dict()}

    def build(self, actor: User) -> Dict[str, Any]:
        require(actor, Role.ANALYST)
        with self._state._lock:
            if self._state.status == "running":
                return self._state.to_dict()
            if self._catalog.status().get("ready"):
                self._state.status, self._state.stage, self._state.error = "done", "", None
                return self._state.to_dict()
            self._state.status, self._state.stage, self._state.error = "running", "Başlıyor", None
            self._state.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        def run():
            try:
                self._catalog.build(progress=lambda s: setattr(self._state, "stage", s))
                self._ws.save()  # sözlük veritabanına; diğer süreçler (worker, diğer API kopyaları) oradan alır
                self._state.status, self._state.stage = "done", ""
            except Exception as exc:  # noqa: BLE001
                self._state.status, self._state.stage, self._state.error = "failed", "", str(exc)

        threading.Thread(target=run, daemon=True, name="catalog-build").start()
        return self._state.to_dict()

    def _ready(self) -> None:
        if not self._catalog.status().get("ready"):
            raise ConflictError("Hastalık sözlüğü henüz indirilmedi.")

    def search(self, query: str, limit: int = 12) -> List[Dict[str, Any]]:
        self._ready()
        with self._uow() as uow:
            existing = {d.mesh_id for d in uow.diseases.list()}
        return [{**m, "already_added": m.get("mesh_id") in existing} for m in self._catalog.search(query, limit)]

    def browse(self, query: str = "", category: str = "", offset: int = 0, limit: int = 200) -> Dict[str, Any]:
        self._ready()
        with self._uow() as uow:
            existing = {d.mesh_id: d.key for d in uow.diseases.list()}
        data = self._catalog.browse(query, category, offset, min(limit, 1000))
        for it in data.get("items", []):
            it["added_key"] = existing.get(it.get("mesh_id"))
        return data

    def article_count(self, term: str, entries: List[str]) -> Dict[str, Any]:
        try:
            return self._catalog.article_count(term, entries)
        except Exception as exc:
            raise ExternalServiceError(f"Makale veritabanına ulaşılamadı: {exc}") from exc

    def resolve(self, name: str) -> List[Dict[str, Any]]:
        if not (name or "").strip():
            raise ValidationError("Hastalık adı gerekli.")
        try:
            return self._catalog.resolve(name.strip())
        except Exception as exc:
            raise ExternalServiceError(f"Hastalık sözlüğü araması başarısız: {exc}") from exc


class ExternalQueries:
    def __init__(self, uow_factory: UnitOfWorkFactory, services: ExternalServices, workspace: WorkspaceSync):
        self._uow, self._services, self._ws = uow_factory, services, workspace

    def health(self) -> Dict[str, Any]:
        checks = self._services.health()
        return {"ok": all(c.get("ok") for c in checks), "checks": checks}

    def indications(self, chembl_id: str) -> Dict[str, Any]:
        cid = (chembl_id or "").upper()
        if not cid.startswith("CHEMBL"):
            raise ValidationError("İlaç veritabanı kimliği bekleniyor.")
        try:
            out = {"chembl_id": cid, "indications": self._services.drug_indications(cid)}
            self._ws.save()  # endikasyon önbelleği ortak dosyadadır
            return out
        except Exception as exc:  # noqa: BLE001
            return {"chembl_id": cid, "indications": None, "error": str(exc)[:200]}

    def pubmed_count(self, disease_key: str) -> Dict[str, Any]:
        with self._uow() as uow:
            d = _get_or_404(uow, disease_key)
        try:
            return {"count": self._services.pubmed_count(d.pubmed_query)}
        except Exception as exc:
            raise ExternalServiceError(f"Makale veritabanına ulaşılamadı: {exc}") from exc

    def estimate(self, disease_key: str, max_a: int) -> Dict[str, Any]:
        with self._uow() as uow:
            d = _get_or_404(uow, disease_key)
        try:
            return self._services.estimate(d, max_a)
        except Exception as exc:
            raise ExternalServiceError(f"Makale veritabanına ulaşılamadı: {exc}") from exc
