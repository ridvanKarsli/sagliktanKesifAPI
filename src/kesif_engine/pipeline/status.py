"""Adım durumları: hangi adımın ne zaman çalıştığı, derlemin özeti."""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from kesif_engine.core.paths import resultsdir, workdir
from kesif_engine.pipeline.settings import load_settings
from kesif_engine.sources import chembl

log = logging.getLogger("kesif")


def _mark(disease: str, step: str, **extra) -> None:
    path = resultsdir(disease) / "status.json"
    status = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    status[step] = {"at": datetime.datetime.now().isoformat(timespec="seconds"), **extra}
    path.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")


def status(disease: str) -> dict:
    wd, rd = workdir(disease), resultsdir(disease)
    st = json.loads((rd / "status.json").read_text(encoding="utf-8")) if (rd / "status.json").exists() else {}
    index = json.loads((wd / "corpus_index.json").read_text(encoding="utf-8")) if (wd / "corpus_index.json").exists() else {}

    def _mtime(path: Path):
        return datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")

    # Adım durumu dosyaların kendisinden türetilir; status.json yalnızca ek bilgi taşır.
    artifacts = {
        "fetch": wd / "corpus_index.json",
        "drugs": chembl.dictionary_path(),
        "labels": wd / "labels.json",
        "evidence": wd / "evidence_external.json",
        "rank": rd / "candidates.csv",
        "backtest": rd / "backtest.json",
        "novelty": rd / "candidates_novelty.csv",
        "report": rd / "report.html",
        "tune": rd / "tuning.json",
    }
    for step, path in artifacts.items():
        if path.exists():
            st.setdefault(step, {})
            st[step].setdefault("at", index.get("fetched_at") if step == "fetch" and index.get("fetched_at") else _mtime(path))
        else:
            st.pop(step, None)
    if index:
        st["fetch"].setdefault("n_articles", len(index.get("a_pmids", [])))
        st["fetch"].setdefault("n_bridges", len(index.get("c_pmids_by_gene", {})))
    return {
        "disease": disease,
        "has_corpus": bool(index),
        "n_articles": len(index.get("a_pmids", [])),
        "n_bridges": len(index.get("c_pmids_by_gene", {})),
        "fetched_at": index.get("fetched_at"),
        "data_until": index.get("data_until"),
        "labels_schema": _labels_schema(wd),
        "has_drugs": chembl.dictionary_path().exists(),
        "has_labels": (wd / "labels.json").exists(),
        "has_candidates": (rd / "candidates.csv").exists(),
        "has_novelty": (rd / "candidates_novelty.csv").exists(),
        "has_backtest": (rd / "backtest.json").exists(),
        "has_report": (rd / "report.html").exists(),
        "has_discovery": (rd / "discovery.json").exists(),
        "has_evidence": (wd / "evidence_external.json").exists(),
        "last_job": json.loads((rd / "last_job.json").read_text(encoding="utf-8")) if (rd / "last_job.json").exists() else None,
        "steps": st,
        "settings": load_settings(disease).to_dict(),
        "confidence": _confidence(disease),
        "label_audit": _audit_summary(wd),
    }


def _labels_schema(wd: Path) -> int:
    path = wd / "labels.json"
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("schema") or 1)
    except (ValueError, json.JSONDecodeError):
        return 0


def _audit_summary(wd: Path) -> dict:
    path = wd / "label_audit.json"
    if not path.exists():
        return {}
    a = json.loads(path.read_text(encoding="utf-8"))
    return {k: a.get(k) for k in ("n_checked", "n_wrong", "error_rate", "at")}


def _confidence(disease: str) -> dict:
    from kesif_engine.pipeline.steps import method_confidence  # döngüsel içe aktarmayı önlemek için geç yükleme

    try:
        return method_confidence(disease)
    except Exception as exc:  # noqa: BLE001
        log.warning("Güven notu hesaplanamadı: %s", exc)
        return {"grade": "ölçülemedi", "reasons": [], "numbers": {}}
