"""Sonuçlar: motor çıktılarının veritabanına aktarılması (SyncResults) ve veritabanından okunması.

Tek gerçek kaynak veritabanıdır. Motorun çalışma dizini yalnızca yeniden üretilebilir bir önbellektir (indirilen
makaleler, ara dosyalar); API hiçbir isteği dosya sisteminden karşılamaz.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional, Tuple

from ...domain.entities import Artifact, ArtifactKind, Candidate, CandidateFilter, Page
from ...domain.errors import NotFoundError
from ...domain.ports import AnalysisEngine, ResultReader, UnitOfWorkFactory
from .diseases import _get_or_404


class SyncResults:
    """Bir analizden (ya da eski kurulumdan) sonra motorun ürettiği her şeyi veritabanına yazar.

    Aday listesi `candidates` tablosuna (hastalık başına tam değiştirme), diğer çıktılar `analysis_artifacts` tablosuna
    (tür başına tek kayıt). Eksik çıktı eski kaydı silmez; yalnızca üretilenler güncellenir.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, reader: ResultReader, engine: AnalysisEngine):
        self._uow, self._reader, self._engine = uow_factory, reader, engine

    def __call__(self, disease_key: str, run_id: Optional[str]) -> Dict[str, Any]:
        rows = self._reader.candidates(disease_key)
        artifacts: List[Artifact] = [Artifact(disease_key, ArtifactKind.STATUS, payload=self._engine.status(disease_key), run_id=run_id)]
        if rows:
            evidence = {r["chemical_id"]: self._reader.evidence(disease_key, r["chemical_id"]) for r in rows}
            artifacts.append(Artifact(disease_key, ArtifactKind.EVIDENCE, payload=evidence, run_id=run_id))
        for kind, value in (
            (ArtifactKind.KNOWN, self._reader.known_therapies(disease_key)),
            (ArtifactKind.BRIDGES, self._reader.bridges(disease_key)),
            (ArtifactKind.BACKTEST, self._reader.backtest(disease_key)),
            (ArtifactKind.TUNING, self._reader.tuning(disease_key)),
            (ArtifactKind.DISCOVERY, self._reader.discovery(disease_key)),
        ):
            if value:
                artifacts.append(Artifact(disease_key, kind, payload=value, run_id=run_id))
        html = self._reader.report_html(disease_key)
        if html:
            artifacts.append(Artifact(disease_key, ArtifactKind.REPORT, text=html, run_id=run_id))
        with self._uow() as uow:
            n = uow.candidates.replace_for_disease(disease_key, run_id, (Candidate.from_row(disease_key, r, run_id) for r in rows)) if rows else 0
            for a in artifacts:
                uow.artifacts.put(a)
            uow.commit()
        return {"candidates": n, "artifacts": len(artifacts)}


class QueryCandidates:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow = uow_factory

    def __call__(self, disease_key: str, flt: CandidateFilter, page: Page) -> Tuple[int, List[Candidate]]:
        with self._uow() as uow:
            _get_or_404(uow, disease_key)
            return uow.candidates.query(disease_key, flt, page)


class GetCandidate:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow = uow_factory

    def __call__(self, disease_key: str, chemical_id: str) -> Dict[str, Any]:
        with self._uow() as uow:
            _get_or_404(uow, disease_key)
            c = uow.candidates.get(disease_key, chemical_id)
            if c is None:
                raise NotFoundError("Aday bulunamadı.", details={"chemical_id": chemical_id})
            ev = uow.artifacts.get(disease_key, ArtifactKind.EVIDENCE)
        return {"candidate": c, "evidence": (ev.payload or {}).get(chemical_id, []) if ev else []}


class ReadResults:
    """Veritabanındaki analiz çıktılarını okur. `reader` yalnızca saf dönüşüm (karşılaştırma satırı) için kullanılır."""

    def __init__(self, uow_factory: UnitOfWorkFactory, reader: ResultReader):
        self._uow, self._reader = uow_factory, reader

    def _artifact(self, disease_key: str, kind: ArtifactKind):
        with self._uow() as uow:
            _get_or_404(uow, disease_key)
            return uow.artifacts.get(disease_key, kind)

    def _payload(self, disease_key: str, kind: ArtifactKind, default):
        a = self._artifact(disease_key, kind)
        return a.payload if a and a.payload is not None else default

    def evidence(self, disease_key: str, chemical_id: str) -> List[Dict[str, Any]]:
        return self._payload(disease_key, ArtifactKind.EVIDENCE, {}).get(chemical_id, [])

    def known(self, disease_key: str) -> List[Dict[str, Any]]:
        return self._payload(disease_key, ArtifactKind.KNOWN, [])

    def backtest(self, disease_key: str) -> Dict[str, Any]:
        return self._payload(disease_key, ArtifactKind.BACKTEST, {})

    def tuning(self, disease_key: str) -> Dict[str, Any]:
        return self._payload(disease_key, ArtifactKind.TUNING, {})

    def bridges(self, disease_key: str) -> List[Dict[str, Any]]:
        return self._payload(disease_key, ArtifactKind.BRIDGES, [])

    def discovery(self, disease_key: str) -> Dict[str, Any]:
        return self._payload(disease_key, ArtifactKind.DISCOVERY, {})

    def status(self, disease_key: str) -> Dict[str, Any]:
        return self._payload(disease_key, ArtifactKind.STATUS, empty_status(disease_key))

    def report_html(self, disease_key: str) -> str:
        a = self._artifact(disease_key, ArtifactKind.REPORT)
        if a is None or not a.text:
            raise NotFoundError("Rapor henüz üretilmedi.")
        return a.text

    def export_csv(self, disease_key: str) -> str:
        with self._uow() as uow:
            _get_or_404(uow, disease_key)
            total, rows = uow.candidates.query(disease_key, CandidateFilter(show_endogenous=True, sort="rank"), Page(0, 1000))
            if total > 1000:
                _, rows = uow.candidates.query(disease_key, CandidateFilter(show_endogenous=True, sort="rank"), Page(0, min(total, 50000)))
        if not rows:
            raise NotFoundError("Aday listesi yok.")
        columns = list(Candidate.FIXED) + sorted({k for r in rows for k in r.extra})
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r.to_row())
        return buf.getvalue()

    def compare(self) -> List[Dict[str, Any]]:
        """Hastalıklar arası karşılaştırma: veritabanındaki keşif özetlerinden."""
        out = []
        with self._uow() as uow:
            for d in uow.diseases.list():
                disc = uow.artifacts.get(d.key, ArtifactKind.DISCOVERY)
                if disc and disc.payload:
                    out.append(self._reader.compare_row(d.key, d.label, disc.payload))
        return out

    def log_tail(self, disease_key: str, tail: int = 200) -> List[str]:
        with self._uow() as uow:
            _get_or_404(uow, disease_key)
            run = uow.runs.latest_for(disease_key)
        if run is None or not run.log:
            return []
        return run.log.splitlines()[-tail:]


def empty_status(disease_key: str) -> Dict[str, Any]:
    return {"disease": disease_key, "has_corpus": False, "n_articles": 0, "n_bridges": 0, "fetched_at": None, "has_drugs": False,
            "has_labels": False, "has_candidates": False, "has_novelty": False, "has_backtest": False, "has_report": False,
            "has_discovery": False, "last_job": None, "steps": {}, "settings": {}}
