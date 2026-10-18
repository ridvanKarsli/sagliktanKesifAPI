"""Analiz çıktılarını okuma (aday listesi, kanıt, bilinen tedaviler, doğrulama)."""

from __future__ import annotations

import json
import logging
import math

import pandas as pd

from kesif_engine.core.paths import resultsdir

log = logging.getLogger("kesif")


# ---------- okuma yardımcıları (web) ----------

def load_candidates(disease: str) -> list:
    out = resultsdir(disease)
    path = out / "candidates.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path, index_col="rank")
    nov = out / "candidates_novelty.csv"
    if nov.exists():
        n = pd.read_csv(nov)[["chemical_id", "clinical_trials", "pubmed_direct", "durum"]]
        df = df.reset_index().merge(n, on="chemical_id", how="left").set_index("rank")
    rows = []
    for rank, r in df.iterrows():
        d = {}
        for k, v in r.to_dict().items():
            if isinstance(v, float) and math.isnan(v):
                v = None
            elif hasattr(v, "item"):
                v = v.item()
            d[k] = v
        d["rank"] = int(rank)
        for k in ("clinical_trials", "pubmed_direct"):
            if d.get(k) is not None:
                d[k] = int(d[k])
        for k in ("aliases", "mechanism", "direct_pmids", "top_bridges", "chembl_id", "durum"):
            if d.get(k) is None:
                d[k] = ""
        # eski sıralama dosyalarıyla uyum: sınıf/durum/ceza sütunları yoksa varsayılanlar
        d.setdefault("kind", "ilaç")
        d.setdefault("status", "tedavi olarak çalışılmış" if (d.get("n_treat_rel") or 0) >= 2 else ("literatürde anılmış" if d.get("n_direct_a") else "yeni"))
        d.setdefault("penalty", 1.0)
        if d.get("kind") is None:
            d["kind"] = "ilaç"
        if d.get("penalty") is None:
            d["penalty"] = 1.0
        # Yenilik kontrolü (PubMed tam sayım + ClinicalTrials.gov) örneklenmiş derlemeden daha güvenilirdir:
        # örneklemde hiç anılmamış görünen ilaç PubMed'de hastalıkla 395 kez geçebilir (saralasin-hipertansiyon).
        if d.get("clinical_trials"):
            d["status"] = "tedavi olarak çalışılmış"
        elif d.get("pubmed_direct") and d.get("status") == "yeni":
            d["status"] = "literatürde anılmış"
        rows.append(d)
    return rows


def load_known(disease: str) -> list:
    path = resultsdir(disease) / "known_therapies.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path, index_col="rank")
    rows = []
    for rank, r in df.iterrows():
        d = {k: (None if isinstance(v, float) and math.isnan(v) else (v.item() if hasattr(v, "item") else v)) for k, v in r.to_dict().items()}
        d["rank"] = int(rank)
        rows.append(d)
    return rows


def load_evidence(disease: str, chemical_id: str) -> list:
    path = resultsdir(disease) / "evidence.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get(chemical_id, [])


def load_backtest(disease: str) -> dict:
    path = resultsdir(disease) / "backtest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
