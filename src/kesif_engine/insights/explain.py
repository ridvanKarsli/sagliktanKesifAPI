"""Kanıt zinciri: "neden bu ilaç?" sorusunun adım adım, kaynaklı cevabı (Faz 3).

Her aday için iki parça üretilir:

    katkılar  Birleşik modelin (benchmark kararı) bu adayı yukarı taşıyan kanalları ve payları. Model ham (log) ölçekli
              lojistik olduğundan katkı = katsayı × (log1p(değer) − log1p(evren ortancası)); yalnız olumlu katkılar
              gösterilir, paylar bunların toplamına oranlanır. Sıklık (popülerlik) de bir kanal olarak görünür: adayın
              yalnızca "çok yazıldığı için" yukarıda olup olmadığı açıkça okunur.
    adımlar   Kanıtın kendisi, kaynak kimlikleriyle:
                literatür köprüsü  ilaç → gen (ortak makaleler) → hastalık (hastalık makaleleri), yön tutarlılığı
                mekanizma          ilaç veritabanında ilacın bu gen üzerindeki etkisi
                benzer hastalık    ilacın denendiği, bu hastalığa en benzer hastalıklar: yıl ve NCT kimliği
                bağımsız kanıt     hedef genin genetik ilişkisi, protein ağında hastalık genlerine yakınlık

Puanı değiştirmez; yalnız açıklar. Aynı veri aynı zinciri verir (belirlenimci).
"""

from __future__ import annotations

import datetime
import json
import math
from typing import Dict, List, Optional

import pandas as pd

CHANNEL_NAMES = {
    "similar": "benzer hastalıklarda denenmiş", "accel": "kanıt ivmesi", "direct": "hastalık literatüründe doğrudan anılma",
    "recent": "son yıllardaki anılma", "lit": "literatür köprüsü", "mech": "mekanizma kaydı", "ltc": "köprü gen sayısı",
    "genetic": "genetik ilişki", "biology": "biyolojik kanıt", "network": "protein ağı yakınlığı",
    "prevalence": "köprü literatüründe yaygınlık", "frequency": "popülerlik (toplam anılma)",
}
DIRECTION = {"consistent": "yön tutarlı", "inconsistent": "yön tutarsız (dikkat)", "unknown": "yön bilinmiyor"}
EFFECT = {"inhibit": "baskılıyor", "activate": "artırıyor"}
TOP_LINKS = 2
TOP_NEIGHBORS = 3
MIN_NETWORK_Z = 2.0  # zincirde gösterilecek en küçük ağ yakınlığı (z < 2 anlamlı yakınlık sayılmaz)


def contributions(model: dict, features: pd.DataFrame) -> pd.DataFrame:
    """Aday × kanal katkı tablosu (yalnız benchmark kararının modeli 'stack' ise)."""
    names = model.get("features") or []
    coef = dict(zip(names, model.get("coef") or []))
    cols = {}
    for n in names:
        if n not in features.columns:
            continue
        t = features[n].astype(float).clip(lower=0).map(math.log1p)
        cols[n] = coef[n] * (t - float(t.median()))
    return pd.DataFrame(cols, index=features.index)


def _shares(row: pd.Series, top: int = 4) -> List[dict]:
    pos = row[row > 0].sort_values(ascending=False)
    total = float(pos.sum())
    if total <= 0:
        return []
    return [{"kanal": k, "ad": CHANNEL_NAMES.get(k, k), "pay": round(float(v) / total, 3)} for k, v in pos.head(top).items()]


def _literature_steps(name: str, disease_label: str, links: List[dict]) -> List[dict]:
    steps = []
    lit = sorted((lk for lk in links if lk.get("source") == "literature"), key=lambda lk: -float(lk.get("contribution") or 0))
    seen = set()
    for lk in lit:
        if lk["gene"] in seen:
            continue
        seen.add(lk["gene"])
        eff = EFFECT.get(lk.get("drug_effect") or "")
        steps.append({
            "tur": "literatür köprüsü",
            "metin": f"{name} → {lk['gene']}: {lk.get('n_bc', 0)} ortak makale (beklenenin {lk.get('lift')} katı)"
                     + (f"; ilaç geni {eff}" if eff else "")
                     + f" → {disease_label}: {lk['gene']} {lk.get('n_ab', 0)} hastalık makalesinde; "
                     + DIRECTION.get(lk.get("consistency") or "unknown", "yön bilinmiyor") + ".",
            "kaynaklar": [{"tip": "pmid", "id": p} for p in (lk.get("bc_pmids") or [])[:2]]
                         + [{"tip": "pmid", "id": p} for p in (lk.get("ab_pmids") or [])[:2]],
        })
        if len(steps) >= TOP_LINKS:
            break
    for lk in links:
        if lk.get("source") == "chembl" and lk.get("gene") and len(steps) < TOP_LINKS + 1:
            steps.append({"tur": "mekanizma",
                          "metin": f"İlaç veritabanına göre {name}, {lk['gene']} için {(lk.get('action_type') or 'etkileşim').lower()} kaydı taşıyor"
                                   + (f" ({lk['moa']})" if lk.get("moa") else "") + ".",
                          "kaynaklar": []})
            break
    return steps


def _neighbor_step(name: str, key: str, external: dict, year: int) -> Optional[dict]:
    atlas = external.get("atlas")
    if atlas is None:
        return None
    _, used = atlas.neighbor_scores(external.get("atlas_own") or {}, external.get("atlas_codes") or [], cutoff=year,
                                    canonical=external.get("atlas_canonical"))
    hits = []
    for u in used:
        y, nct = atlas.source(u["mesh"], key)
        if y is not None and y <= year:
            hits.append((u, y, nct))
        if len(hits) >= TOP_NEIGHBORS:
            break
    if not hits:
        return None
    parts = [f"{u['name'] or u['mesh']} ({y}; benzerlik {u['similarity']:.2f})" for u, y, _ in hits]
    return {"tur": "benzer hastalık",
            "metin": f"{name}, bu hastalığa benzeyen hastalıklarda klinik çalışmaya girmiş: " + ", ".join(parts)
                     + ". Benzerlik, iki hastalıkta denenmiş ilaçların örtüşmesinden.",
            "kaynaklar": [{"tip": "nct", "id": nct} for _, _, nct in hits if nct]}


def _z(text: str) -> float:
    try:
        return float(text.rsplit("z=", 1)[1])
    except (IndexError, ValueError):
        return 0.0


def _independent_steps(row: dict) -> List[dict]:
    steps = []
    gt, nt = row.get("genetic_target"), row.get("network_target")
    if isinstance(gt, str) and gt:
        sym, val = gt.rsplit(" ", 1)
        steps.append({"tur": "genetik", "metin": f"Hedef gen {sym} hastalıkla genetik olarak ilişkili (Open Targets skoru {val}).",
                      "kaynaklar": []})
    if isinstance(nt, str) and nt and _z(nt) >= MIN_NETWORK_Z:
        sym, val = nt.rsplit(" ", 1)
        steps.append({"tur": "protein ağı", "metin": f"Hedef {sym}, protein ağında hastalık genlerine rastgele beklenenden yakın ({val}).",
                      "kaynaklar": []})
    return steps


def build(ranked: pd.DataFrame, features: pd.DataFrame, decision: dict, evidence: Dict[str, List[dict]], external: dict,
          drug_map: dict, disease_label: str, top: int = 100, use_dict: bool = True) -> Dict[str, dict]:
    """{chemical_id: {"katkilar": [...], "adimlar": [...]}} sıralamanın ilk `top` adayı için."""
    model = decision.get("model") if decision.get("method") == "stack" else None
    contrib = contributions(model, features) if model else pd.DataFrame()
    year = datetime.date.today().year
    out: Dict[str, dict] = {}
    for row in ranked.head(top).to_dict("records"):
        cid = row["chemical_id"]
        key = drug_map.get(cid, cid) if use_dict else cid
        name = row.get("chemical") or cid
        steps = _literature_steps(name, disease_label, evidence.get(cid) or [])
        nb = _neighbor_step(name, key, external, year)
        if nb:
            steps.insert(0 if (len(contrib) and key in contrib.index and contrib.loc[key].get("similar", 0) > 0) else len(steps), nb)
        steps += _independent_steps(row)
        out[cid] = {"katkilar": _shares(contrib.loc[key]) if len(contrib) and key in contrib.index else [],
                    "adimlar": steps}
    return out


def to_column(chains: Dict[str, dict], ids) -> List[str]:
    return [json.dumps(chains[c], ensure_ascii=False, separators=(",", ":")) if c in chains else "" for c in ids]
