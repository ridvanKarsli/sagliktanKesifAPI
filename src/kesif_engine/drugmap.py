"""Makalelerdeki kimyasal kimliklerini (PubTator/MeSH) ChEMBL ilaçlarına ad üzerinden eşler.

Eşleme kuralı (sırayla): tercih edilen ad tam eşleşir > eş ad tam eşleşir (en az 5 karakter).
Birden fazla ilaç eşleşirse en yüksek klinik fazlı olan seçilir. Kısa kısaltmalar (ADA, UST) eşlenmez.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Optional

from . import config

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")


def norm(name: str) -> str:
    return _WS.sub(" ", (name or "").strip().lower())


class DrugIndex:
    def __init__(self, drugs: list):
        self.by_id = {d["chembl_id"]: d for d in drugs}
        self.pref: dict = {}
        self.syn: dict = {}
        for d in drugs:
            if d.get("name"):
                self.pref.setdefault(norm(d["name"]), []).append(d["chembl_id"])
            for s in d.get("synonyms", []):
                self.syn.setdefault(norm(s), []).append(d["chembl_id"])

    def _best(self, ids: list):
        return max(ids, key=lambda i: (self.by_id[i]["max_phase"], len(self.by_id[i]["mechanisms"]), i))

    def match(self, labels) -> str:
        """Bir kimyasal için görülen yazımlardan (sık olandan seyreğe) ilaç kimliği seçer; yoksa ''.

        Durdurma listesindeki etiketler (endojen metabolit, reaktif, boya) ve '&' içeren kısaltmalar eşlenmez.
        """
        labels = [l for l in labels if norm(l) not in config.STOP_CHEMICALS and "&" not in l]
        for label in labels:
            key = norm(label)
            if len(key) >= 3 and key in self.pref:
                return self._best(self.pref[key])
        for label in labels:
            key = norm(label)
            if len(key) >= 5 and key in self.syn:
                return self._best(self.syn[key])
        return ""


def build_map(votes: dict, drugs: list, manual: Optional[dict] = None, min_phase: Optional[float] = None) -> dict:
    """votes: chem_id -> Counter(label -> sayı). Dönen: chem_id -> chembl_id (yalnızca eşleşenler)."""
    min_phase = config.MIN_PHASE if min_phase is None else min_phase
    index = DrugIndex([d for d in drugs if d["max_phase"] >= min_phase])
    out: dict = {}
    for cid, counter in votes.items():
        labels = [lab for lab, _ in counter.most_common()] if isinstance(counter, Counter) else list(counter)
        hit = index.match(labels)
        if hit:
            out[cid] = hit
    for cid, chembl_id in (manual or {}).items():
        if chembl_id:
            out[cid] = chembl_id
        else:
            out.pop(cid, None)
    log.info("%d / %d kimyasal ChEMBL ilacına eşlendi", len(out), len(votes))
    return out


def load_manual(disease_dir) -> dict:
    """data/<hastalık>/drug_map_manual.json: {"MESH:...": "CHEMBL..."} veya {"MESH:...": null} (eşlemeyi iptal eder)."""
    path = disease_dir / "drug_map_manual.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}
