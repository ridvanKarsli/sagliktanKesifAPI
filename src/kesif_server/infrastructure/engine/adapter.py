"""`kesif_engine` için port uygulamaları: AnalysisEngine, ResultReader, DiseaseCatalog, ExternalServices.

Motor dosya tabanlı çalışır (data_dir / results_dir). Bu adaptör:
- hastalık tanımlarını veritabanından motora sağlar (`diseases.set_provider`),
- iş günlüğünü iş parçacığına göre süzüp dosyaya yazar ve ilerlemeye çevirir,
- motorun pandas/NaN değerlerini JSON'a uygun hale getirir.
"""

from __future__ import annotations

import contextlib
import logging
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from kesif_engine import chembl, discovery, mesh, pipeline, pubmed, services
from kesif_engine import config as engine_config
from kesif_engine import diseases as engine_diseases
from kesif_engine.net import HttpClient
from kesif_engine.ranking import Params, filter_specific, select_bridges, specificity

from ...domain.entities import Disease, RunProgress
from ...domain.errors import ValidationError
from .progress import ProgressTracker

log = logging.getLogger(__name__)
_engine_lock = threading.Lock()  # motor modül düzeyinde durum taşır; aynı süreçte iki iş paralel koşmamalı


def configure_engine(data_dir: Path, results_dir: Path, disease_provider: Callable[[], Dict[str, Dict[str, str]]],
                     ncbi_api_key: Optional[str] = None, ncbi_email: Optional[str] = None) -> None:
    engine_config.configure(data_dir, results_dir)
    if ncbi_api_key:
        engine_config.NCBI_API_KEY = ncbi_api_key
    if ncbi_email:
        engine_config.NCBI_EMAIL = ncbi_email
    engine_diseases.set_provider(disease_provider)


def _clean(value: Any) -> Any:
    """pandas/numpy değerlerini ve NaN'ı JSON'a uygun Python değerlerine çevirir."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


class _ThreadLogHandler(logging.Handler):
    """Yalnızca işi çalıştıran iş parçacığının kayıtlarını dosyaya yazar ve ilerleme izleyiciye iletir."""

    def __init__(self, thread_id: int, log_path: Path, tracker: ProgressTracker):
        super().__init__()
        self._tid, self._path, self._tracker = thread_id, log_path, tracker
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self._tid:
            return
        line = self.format(record)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        with contextlib.suppress(Exception):  # ilerleme çevirisi işi düşürmemeli
            self._tracker.feed(record.getMessage())


class EngineAdapter:
    """AnalysisEngine port'u."""

    def run(self, disease: Disease, steps: List[str], params: Dict[str, Any], options: Dict[str, Any],
            progress: Callable[[RunProgress], None], log_path: Path) -> Dict[str, Any]:
        tracker = ProgressTracker(progress, steps)
        handler = _ThreadLogHandler(threading.get_ident(), log_path, tracker)
        root = logging.getLogger()
        root.addHandler(handler)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {disease.key}: {', '.join(steps)} =====\n")
        try:
            with _engine_lock:
                summary = pipeline.run_steps(
                    disease.key, steps, Params.from_dict(params),
                    cutoff=options.get("cutoff", 2018), top=options.get("top", 100),
                    fetch_opts={k: options[k] for k in ("start_year", "max_a", "per_gene", "cutoff_a", "cutoff_b") if k in options},
                    force=options.get("force", False),
                )
            tracker.finish()
            return _clean(summary)
        finally:
            root.removeHandler(handler)

    def rerank(self, disease: Disease, params: Dict[str, Any], cutoff: Optional[int]) -> Dict[str, Any]:
        p = Params.from_dict(params)
        pipeline.save_settings(disease.key, p)
        try:
            corpus = pipeline.Corpus(disease.key)
        except FileNotFoundError as exc:
            raise ValidationError(str(exc)) from exc
        with _engine_lock:
            ranked = pipeline.step_rank(disease.key, p, corpus)
            result: Dict[str, Any] = {"n_candidates": len(ranked)}
            if cutoff:
                bt = pipeline.step_backtest(disease.key, cutoff, p, corpus)
                result["backtest"] = {"primary_scheme": bt.get("primary_scheme"),
                                      "schemes": {s: r.get("metrics") for s, r in bt["schemes"].items()}}
            pipeline.step_report(disease.key)
        return _clean(result)

    def default_params(self) -> Dict[str, Any]:
        return Params().to_dict()

    def validate_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        unknown = sorted(set(params or {}) - set(Params.FIELDS))
        if unknown:
            raise ValidationError(f"Bilinmeyen parametre: {', '.join(unknown)}", details={"unknown": unknown})
        try:
            return Params.from_dict(params or {}).to_dict()
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Parametre değeri geçersiz: {exc}") from exc

    def status(self, disease_key: str) -> Dict[str, Any]:
        return _clean(pipeline.status(disease_key))

    def is_network_error(self, exc: BaseException) -> bool:
        if isinstance(exc, (requests.RequestException, ConnectionError, TimeoutError)):
            return True
        text = str(exc)
        return isinstance(exc, RuntimeError) and ("İstek başarısız" in text or "HTTP 5" in text or "HTTP 429" in text)


class ResultReaderAdapter:
    """ResultReader port'u: motorun sonuç dosyaları."""

    def candidates(self, disease_key: str) -> List[Dict[str, Any]]:
        return _clean(pipeline.load_candidates(disease_key))

    def evidence(self, disease_key: str, chemical_id: str) -> List[Dict[str, Any]]:
        return _clean(pipeline.load_evidence(disease_key, chemical_id))

    def known_therapies(self, disease_key: str) -> List[Dict[str, Any]]:
        return _clean(pipeline.load_known(disease_key))

    def backtest(self, disease_key: str) -> Dict[str, Any]:
        return _clean(pipeline.load_backtest(disease_key))

    def tuning(self, disease_key: str) -> Dict[str, Any]:
        import json

        path = pipeline.resultsdir(disease_key) / "tuning.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def bridges(self, disease_key: str) -> List[Dict[str, Any]]:
        try:
            corpus = pipeline.Corpus(disease_key)
        except FileNotFoundError:
            return []
        p = pipeline.load_settings(disease_key)  # son çalıştırmanın parametreleri (çalışma dizini önbelleği)
        fetched = set(corpus.index.get("c_pmids_by_gene", {})) | set(corpus.ortholog_map.values())
        species: Dict[Any, str] = {9606: "insan", 10090: "fare", 10116: "sıçan"}
        bridges = select_bridges(corpus.a, p)
        kept, _ = filter_specific(bridges, len(corpus.a), corpus.gene_counts, p.min_spec)
        kept_ids = {b[0] for b in kept}
        out = []
        for gid, label, n, source in bridges:
            info = corpus.gene_info.get(gid) or {}
            spec = specificity(n, len(corpus.a), corpus.gene_counts, gid)
            out.append({"gene_id": gid, "label": label, "symbol": info.get("symbol", ""), "n_articles": n, "source": source,
                        "species": species.get(info.get("taxid"), "diğer" if info.get("taxid") else ""),
                        "specificity": round(spec, 1) if spec is not None else None, "used": gid in kept_ids,
                        "has_literature": gid in fetched or gid not in kept_ids, "n_literature": len(corpus.c.get(gid, []))})
        return _clean(out)

    def discovery(self, disease_key: str) -> Dict[str, Any]:
        return _clean(discovery.load_summary(pipeline.resultsdir(disease_key)))

    def compare_row(self, key: str, label: str, summary: Dict[str, Any]) -> Dict[str, Any]:
        return _clean(discovery.compare_row(key, label, summary))

    def report_html(self, disease_key: str) -> Optional[str]:
        path = pipeline.resultsdir(disease_key) / "report.html"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def log_tail(self, disease_key: str, tail: int) -> List[str]:
        path = pipeline.resultsdir(disease_key) / "job.log"
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()[-tail:]


class CatalogAdapter:
    """DiseaseCatalog port'u (MeSH sözlüğü)."""

    def status(self) -> Dict[str, Any]:
        return mesh.status()

    def build(self, progress: Callable[[str], None]) -> None:
        mesh._cache.clear()
        mesh.build(HttpClient(), progress=progress)

    def search(self, query: str, limit: int) -> List[Dict[str, Any]]:
        return [{**m, "pubmed_query": mesh.pubmed_query(m["term"], m["entries"])} for m in mesh.search(query, limit)]

    def browse(self, query: str, category: str, offset: int, limit: int) -> Dict[str, Any]:
        data = mesh.browse(query, category, offset, limit)
        for it in data.get("items", []):
            it["pubmed_query"] = mesh.pubmed_query(it["term"], it["entries"])
        return data

    def article_count(self, term: str, entries: List[str]) -> Dict[str, Any]:
        query = mesh.pubmed_query(term, entries)
        return {"count": pubmed.count(HttpClient(), query), "query": query}

    def resolve(self, name: str) -> List[Dict[str, Any]]:
        matches = engine_diseases.resolve_mesh(HttpClient(), name)
        return [{**m, "pubmed_query": engine_diseases.build_query(m["term"], m["term"])} for m in matches]

    def build_query(self, mesh_term: str, name_en: str) -> str:
        return engine_diseases.build_query(mesh_term, name_en)

    def slugify(self, text: str) -> str:
        return engine_diseases.slugify(text)


class ExternalServicesAdapter:
    """ExternalServices port'u."""

    def health(self) -> List[Dict[str, Any]]:
        return services.check_all()

    def drug_indications(self, chembl_id: str) -> List[Dict[str, Any]]:
        return chembl.fetch_indications(HttpClient(timeout=20, retries=1), chembl_id)

    def pubmed_count(self, query: str) -> int:
        return pubmed.count(HttpClient(), query)

    def estimate(self, disease: Disease, max_a: int) -> Dict[str, Any]:
        return _clean(pipeline.estimate(disease.key, max_a=max_a))
