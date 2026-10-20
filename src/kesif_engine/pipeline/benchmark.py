"""Yöntem karnesi adımı: derlemi hazır tüm hastalıklarda çok kesitli ölçüm ve birleştirme kararı.

Çıktılar (ortak, tüm hastalıklar için): data/benchmark/benchmark.json, data/benchmark/fusion_model.json.
Karar dosyası yalnızca ölçüm yapılabildiyse yazılır; yazıldığında sonraki sıralamalar onu kullanır.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Optional

from kesif_engine.catalog import diseases
from kesif_engine.core import config
from kesif_engine.core.paths import workdir
from kesif_engine.evaluation import benchmark
from kesif_engine.pipeline.corpus import Corpus
from kesif_engine.pipeline.settings import load_settings
from kesif_engine.scoring import fusion

log = logging.getLogger("kesif")


def benchmark_dir() -> Path:
    path = config.DATA_DIR / "benchmark"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_panel() -> list:
    path = config.PACKAGE_DIR / "resources" / "benchmark_panel.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def load_benchmark() -> dict:
    path = benchmark_dir() / "benchmark.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def ready_diseases() -> list:
    """Derlemi ve doğrulama etiketi olan hastalıklar."""
    out = []
    for key in sorted(diseases.load_all()):
        wd = config.DATA_DIR / key
        if (wd / "corpus_index.json").exists() and (wd / "labels.json").exists():
            out.append(key)
    return out


def step_benchmark(keys: Optional[Iterable[str]] = None, hold_out: Optional[Iterable[str]] = None, cutoffs=benchmark.CUTOFFS,
                   apply: bool = True) -> dict:
    keys = sorted(keys or ready_diseases())
    if hold_out is None:
        hold_out = [p["key"] for p in load_panel() if p.get("hold_out")]
    inputs = {}
    for key in keys:
        try:
            c = Corpus(key)
        except FileNotFoundError:
            log.warning("%s: derlem yok, ölçüme alınmadı.", key)
            continue
        c.ensure_gene_counts()
        inputs[key] = {"a": c.a, "c": c.c, "labels": c.labels, "params": load_settings(key), "kwargs": c.kwargs(),
                       "external": c.external, "audit": c.audit}
        log.info("Ölçüme alındı: %s (%d makale)", key, len(c.a))
    result = benchmark.run(inputs, hold_out=hold_out, cutoffs=cutoffs, progress=lambda m: log.info("  geriye dönük test: %s", m))
    out = benchmark_dir()
    (out / "benchmark.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    decision = result["decision"]
    result["applied"] = False
    if apply and decision.get("verdict") in ("benimsendi", "korundu"):
        fusion.model_path(config.DATA_DIR).write_text(json.dumps(decision, ensure_ascii=False, indent=1), encoding="utf-8")
        result["applied"] = True
    log.info("Yöntem karnesi: %s — %s", decision.get("verdict"), decision.get("reason"))
    for key in inputs:
        (workdir(key) / "benchmark_ref.json").write_text(json.dumps(result["diseases"].get(key, {}), ensure_ascii=False), encoding="utf-8")
    return result


# ---------- etiket denetimi ----------

def export_label_audit(key: str, n: int = 50) -> str:
    """Hastalığın klinik çalışma etiketlerinden rastgele n satır (CSV, ; ayraçlı, kaynak çalışma bağlantısıyla)."""
    from kesif_engine.evaluation import audit, labels
    from kesif_engine.sources import chembl

    lab = labels.load(workdir(key) / "labels.json")
    if not lab:
        raise FileNotFoundError(f"{key}: doğrulama etiketi yok; önce analizi çalıştırın.")
    if lab.get("schema", 1) < labels.SCHEMA or not lab.get("first_trial_source"):
        raise ValueError(f"{key}: etiketler eski şemada (kaynak çalışma bilgisi yok); analizi 'Doğrulama verileri' adımıyla yenileyin.")
    return audit.to_csv(audit.sample(lab, chembl.load(), n))


def import_label_audit(key: str, text: str) -> dict:
    """İşaretlenmiş CSV'yi okur, önceki turlarla birleştirir ve kaydeder."""
    from kesif_engine.evaluation import audit

    wd = workdir(key)
    merged = audit.merge(audit.load(wd), audit.read_marked(text))
    audit.save(wd, merged)
    log.info("%s etiket denetimi: %d satır, %d yanlış (hata %s)", key, merged["n_checked"], merged["n_wrong"], merged["error_rate"])
    return merged
