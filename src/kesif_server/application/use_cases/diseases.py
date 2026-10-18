"""Hastalık kullanım senaryoları."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ...domain.entities import RESERVED_DISEASE_KEYS, AnalysisRun, ArtifactKind, Disease, Role, User
from ...domain.errors import ConflictError, NotFoundError, ValidationError
from ...domain.ports import DiseaseCatalog, UnitOfWorkFactory, WorkspaceSync
from ..authorization import require


@dataclass
class DiseaseView:
    disease: Disease
    status: Dict[str, Any]
    active_run: Optional[AnalysisRun]
    n_candidates: int


def _get_or_404(uow, key: str) -> Disease:
    d = uow.diseases.get(key)
    if d is None or d.is_removed:
        raise NotFoundError(f"Hastalık bulunamadı: {key}", details={"key": key})
    return d


def _status(uow, d: Disease) -> Dict[str, Any]:
    """Durum anlık görüntüsü veritabanından (son eşitlemede yazılır); hiç analiz yoksa boş durum."""
    from .candidates import empty_status

    a = uow.artifacts.get(d.key, ArtifactKind.STATUS)
    st = dict(a.payload) if a and a.payload else empty_status(d.key)
    last = uow.runs.latest_for(d.key)
    st["last_job"] = ({"status": last.status.value, "steps": [s.value for s in last.steps], "error": last.error,
                       "started_at": last.started_at.isoformat() if last.started_at else None,
                       "finished_at": last.finished_at.isoformat() if last.finished_at else None} if last and last.status.is_terminal else None)
    st["settings"] = d.params or {}
    return st


def _view(uow, d: Disease) -> DiseaseView:
    return DiseaseView(d, _status(uow, d), uow.runs.active_for(d.key), uow.candidates.count_for(d.key))


class ListDiseases:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow = uow_factory

    def __call__(self) -> List[DiseaseView]:
        with self._uow() as uow:
            return [_view(uow, d) for d in uow.diseases.list()]


class GetDisease:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow = uow_factory

    def __call__(self, key: str) -> DiseaseView:
        with self._uow() as uow:
            return _view(uow, _get_or_404(uow, key))


class AddDisease:
    def __init__(self, uow_factory: UnitOfWorkFactory, catalog: DiseaseCatalog):
        self._uow = uow_factory
        self._catalog = catalog

    def __call__(self, actor: User, *, name_en: str, key: Optional[str] = None, label: Optional[str] = None,
                 mesh_id: str = "", mesh_term: str = "", pubmed_query: str = "") -> Disease:
        require(actor, Role.ANALYST)
        name_en = (name_en or "").strip()
        if not name_en:
            raise ValidationError("İngilizce hastalık adı gerekli.")
        slug = self._catalog.slugify(key or name_en)
        if slug in RESERVED_DISEASE_KEYS:
            raise ValidationError(f"'{slug}' sistem tarafından kullanılan bir ad; başka bir anahtar seçin.", details={"key": slug})
        query = pubmed_query.strip() or self._catalog.build_query(mesh_term, name_en)
        with self._uow() as uow:
            existing = uow.diseases.get(slug)
            if existing and not existing.is_removed:
                raise ConflictError(f"'{slug}' zaten tanımlı.", details={"key": slug})
            if existing:  # daha önce kaldırılmış: geri getir ve güncelle
                existing.is_removed = False
                existing.label, existing.name_en, existing.mesh_id, existing.pubmed_query = label or name_en, name_en, mesh_id, query
                disease = uow.diseases.update(existing)
            else:
                disease = uow.diseases.add(Disease(key=slug, label=label or name_en, name_en=name_en, mesh_id=mesh_id,
                                                   pubmed_query=query, created_by=actor.id))
            uow.commit()
            return disease


class RemoveDisease:
    def __init__(self, uow_factory: UnitOfWorkFactory, workspace: WorkspaceSync):
        self._uow = uow_factory
        self._ws = workspace

    def __call__(self, actor: User, key: str, purge: bool = False) -> Dict[str, Any]:
        require(actor, Role.ADMIN if purge else Role.ANALYST)
        with self._uow() as uow:
            d = _get_or_404(uow, key)
            if uow.runs.active_for(key):
                raise ConflictError("Bu hastalık için çalışan ya da sırada bekleyen bir analiz var; önce onu iptal edin.")
            d.is_removed = True
            uow.diseases.update(d)
            purged: List[str] = []
            if purge:
                n_c = uow.candidates.delete_for_disease(key)
                n_a = uow.artifacts.delete_for_disease(key)
                purged = [f"candidates:{n_c}", f"artifacts:{n_a}"]
            uow.commit()
        if purge:
            purged.append(f"files:{self._ws.discard(key)}")
        return {"removed": key, "purged": purged}


class SeedDefaultDiseases:
    """Paketle gelen varsayılan kataloğu veritabanına bir kez yazar (varsa dokunmaz). Açılışta ve migration sonrası çağrılır."""

    def __init__(self, uow_factory: UnitOfWorkFactory, defaults: Dict[str, Dict[str, str]]):
        self._uow = uow_factory
        self._defaults = defaults

    def __call__(self) -> int:
        added = 0
        with self._uow() as uow:
            for key, cfg in self._defaults.items():
                if uow.diseases.get(key) is None:
                    uow.diseases.add(Disease(key=key, label=cfg.get("label", key), name_en=cfg.get("name_en", key),
                                             mesh_id=cfg.get("mesh_id", ""), pubmed_query=cfg.get("pubmed_query", ""), is_default=True))
                    added += 1
            uow.commit()
        return added
