"""Klinik çalışma atlasının derlenmesi: ham ClinicalTrials.gov kayıtlarından "ilaç × hastalık (MeSH) × ilk yıl".

Etiket kurallarını (evaluation.labels) kullandığı için değerlendirme katmanındadır; indirme ve okuma
sources.trial_atlas'tadır.
"""

from __future__ import annotations

import datetime
import gzip
import json
import logging
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

from kesif_engine.evaluation import labels as lab
from kesif_engine.extraction.drugmap import DrugIndex
from kesif_engine.scoring import drugspace
from kesif_engine.sources.chembl import canonical_ids
from kesif_engine.sources.clinicaltrials import trial_counts
from kesif_engine.sources.trial_atlas import SCHEMA, atlas_path

log = logging.getLogger(__name__)


def build(trials: Iterable[dict], drugs: list, min_phase: float, out: Optional[Path] = None,
          progress: Optional[Callable[[str], None]] = None) -> dict:
    """Ham çalışmalardan atlas. Etiket kurallarının hastalıktan bağımsız olanları uygulanır: girişimsel ve tedavi/önleme
    amaçlı, en fazla MAX_CONDITIONS hastalık, sağlıklı gönüllü / farmakokinetik / işlem / eşlik eden hastalık / bırakma /
    destek çalışması değil; test edilen ilaçlar labels.trial_drugs ile (plasebo, karşılaştırma ve arka plan kolları hariç)."""
    say = progress or (lambda m: log.info(m))
    eligible = [d for d in drugs if d["max_phase"] >= min_phase]
    index = DrugIndex(eligible)
    canonical = canonical_ids(drugs)
    excluded = {canonical.get(d["chembl_id"], d["chembl_id"]) for d in eligible
                if drugspace.classify(d, d.get("name", "")) == drugspace.KIND_DIAGNOSTIC}
    first: Dict[str, Dict[str, int]] = {}
    names: Dict[str, str] = {}
    stats = {"total": 0, "used": 0, "no_year": 0, "not_treatment": 0, "too_many_conditions": 0, "no_mesh": 0,
             "no_drug": 0}
    for t in trials:
        stats["total"] += 1
        if stats["total"] % 50000 == 0:
            say(f"  {stats['total']:,} çalışma işlendi")
        y = t.get("start_year")
        if not y:
            stats["no_year"] += 1
            continue
        if not trial_counts(t):
            stats["not_treatment"] += 1
            continue
        if len(t.get("conditions") or []) > lab.MAX_CONDITIONS:
            stats["too_many_conditions"] += 1
            continue
        reason = lab.exclusion_reason(t, ())
        if reason:
            stats[reason] = stats.get(reason, 0) + 1
            continue
        mesh = t.get("condition_mesh_direct") or []
        if not mesh:
            stats["no_mesh"] += 1
            continue
        hits = lab.trial_drugs(t, index, canonical, excluded)
        if not hits:
            stats["no_drug"] += 1
            continue
        stats["used"] += 1
        for code, term in mesh:
            names.setdefault(code, term)
            row = first.setdefault(code, {})
            for cid in hits:
                if y < row.get(cid, 9999):
                    row[cid] = y
    atlas = {"schema": SCHEMA, "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
             "n_trials": stats["used"], "stats": stats, "names": names, "first_year": first}
    out = out or atlas_path()
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump(atlas, fh, ensure_ascii=False)
    say(f"Atlas: {len(first):,} hastalık (MeSH), {stats['used']:,} çalışma kullanıldı; elenenler: "
        + ", ".join(f"{k} {v:,}" for k, v in stats.items() if k not in ("total", "used") and v))
    return atlas
