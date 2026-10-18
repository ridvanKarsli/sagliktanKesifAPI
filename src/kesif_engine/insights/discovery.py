"""Keşif özeti: bir analizin "yeni" adaylarını, bağımsız doğrulamayı ve hedef sınıflarını tek yerde toplar.

Sekiz hastalık turunda elle yapılan iş buradaki koda alındı:
  * Yeni adaylar (hastalıkla hiç anılmamış, ilaç sınıfı) ve her biri için kural tabanlı Türkçe gerekçe
    (hangi gen, kaç makale, yön, güvenlik, mekanizma) — dil modeli yok, her cümle verideki bir sayıya dayanır.
  * Bağımsız doğrulama: yeni adayların kaçı ClinicalTrials.gov'da bu hastalık için kayıtlı (yenilik kontrolünden);
    rastgele beklenen oranla karşılaştırma (hastalık için çalışması olan ilaç sayısı / evren).
  * Hedef sınıfları: yeni adayların köprü genlere göre kümeleri ("IL-23 ekseni: 4 aday").
  * Güvenilirlik testinin özeti.
Çıktı: results/<hastalık>/discovery.json; rapor ve arayüz bunu okur. Hastalıklar arası karşılaştırma compare() ile.
"""

from __future__ import annotations

import datetime
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

TR_DIR = {"up": "artmış", "down": "azalmış", "mutated": "yalnızca ilişki raporu var, yön verisi yok", "unknown": "yönü bilinmiyor"}
TR_EFF = {"inhibit": "baskılıyor", "activate": "artırıyor", "unknown": "etkisi bilinmiyor"}
TR_CONS = {"consistent": "tutarlı", "opposite": "ters", "unknown": "bilinmiyor"}


def _read_csv(path: Path):
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _nan(v):
    return v is None or (isinstance(v, float) and math.isnan(v))


def justify(row: Dict[str, Any], links: list, disease_label: str) -> str:
    """Bir aday için kural tabanlı gerekçe paragrafı."""
    name = row["chemical"]
    phase: Any = row.get("max_phase")
    phase_txt = f"klinik faz {float(phase):.0f}" if not _nan(phase) and phase != "" else "faz bilgisi yok"
    pm: Any = row.get("pubmed_direct")
    ct: Any = row.get("clinical_trials")
    if not _nan(pm) and int(pm) > 0:
        first = f"{name} ({phase_txt}) incelenen {disease_label} makale örneğinde hiç anılmamış; PubMed'in tamamında hastalıkla {int(pm)} makalede birlikte geçiyor."
    else:
        first = f"{name} ({phase_txt}) {disease_label} makalelerinde hiç anılmamış."
    if not _nan(ct) and int(ct) > 0:
        first += f" ClinicalTrials.gov'da bu hastalık için {int(ct)} çalışma kaydı var: yöntem bu ilacı görmeden buldu."
    parts = [first]
    lit = []
    seen_genes: set = set()
    for l in links:
        if l.get("source") == "literature" and l["gene"] not in seen_genes:
            seen_genes.add(l["gene"])
            lit.append(l)
    lit = lit[:3]
    mech = [l for l in links if l.get("source") == "chembl"][:2]
    for l in lit:
        parts.append(
            f"{l['gene']} geni {l['n_ab']} hastalık makalesinde geçiyor ({TR_DIR.get(l.get('gene_direction'), 'yönü bilinmiyor')}"
            f"{', komşu genlerden türetildi' if l.get('gene_direction_source') == 'propagated' else ''}); "
            f"{name} bu genle {l['n_bc']} makalede, beklenenin {l['lift']} katı sıklıkla birlikte anılıyor"
            f"{' ve geni ' + TR_EFF[l['drug_effect']] if l.get('drug_effect') in ('inhibit', 'activate') else ''}"
            f" (yön: {TR_CONS.get(l.get('consistency'), 'bilinmiyor')})."
        )
    for m in mech:
        parts.append(
            f"ChEMBL'e göre {name}, {m['gene']} için {(m.get('action_type') or '').lower() or 'etkileşim'} kaydı taşıyor"
            f"{' (' + m['moa'] + ')' if m.get('moa') else ''}; gen hastalıkta {TR_DIR.get(m.get('gene_direction'), 'yönü bilinmiyor')}"
            f" (yön: {TR_CONS.get(m.get('consistency'), 'bilinmiyor')})."
        )
    gt, nt = row.get("genetic_target"), row.get("network_target")
    if isinstance(gt, str) and gt:
        sym, val = gt.rsplit(" ", 1)
        parts.append(f"Literatürden bağımsız genetik kanıt: hedefi {sym} hastalıkla genetik ilişkili (skor {val}).")
    if isinstance(nt, str) and nt:
        sym, val = nt.rsplit(" ", 1)
        parts.append(f"Protein ağında hedefi {sym} hastalık genlerine rastgele beklenenden yakın ({val.replace('z=', 'z = ')}).")
    harm = row.get("harm_signal") or "yok"
    if harm == "yok":
        parts.append("Hastalıkla doğrudan ilişki raporu yok; güvenlik sinyali değerlendirilemedi.")
    elif harm == "tetikleme sinyali":
        parts.append(f"Dikkat: makalelerde ilacın hastalığı tetiklediği {row.get('n_harm_rel', 0)} rapor var; puan düşürüldü.")
    else:
        parts.append(f"Hastalıkla ilişki raporları: tedavi yönü {row.get('n_treat_rel', 0)}, tetikleme {row.get('n_harm_rel', 0)}.")
    if float(row.get("lit_score") or 0) == 0 and float(row.get("mech_score") or 0) > 0:
        parts.append("Literatür köprüsü yok; kanıt yalnızca mekanizma kaydına dayanıyor (tek kaynaklı).")
    return " ".join(parts)


def build_summary(results_dir: Path, data_dir: Path, disease_cfg: dict, top_new: int = 20, confidence: Optional[dict] = None) -> dict:
    cand = _read_csv(results_dir / "candidates.csv")
    if cand.empty:
        return {}
    nov = _read_csv(results_dir / "candidates_novelty.csv")
    ev_path = results_dir / "evidence.json"
    evidence = json.loads(ev_path.read_text(encoding="utf-8")) if ev_path.exists() else {}
    bt_path = results_dir / "backtest.json"
    bt = json.loads(bt_path.read_text(encoding="utf-8")) if bt_path.exists() else {}
    labels_path = data_dir / "labels.json"
    labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}

    if "kind" not in cand.columns:
        cand["kind"] = "ilaç"
    if "status" not in cand.columns:
        cand["status"] = "yeni"
    if not nov.empty:
        cand = cand.merge(nov[["chemical_id", "clinical_trials", "pubmed_direct", "durum"]], on="chemical_id", how="left")
    else:
        cand["clinical_trials"] = float("nan")
        cand["pubmed_direct"] = float("nan")
        cand["durum"] = None

    counts = {"kind": dict(Counter(cand["kind"])), "status": dict(Counter(cand["status"]))}
    drugs = cand[cand["kind"] == "ilaç"]

    # İki katman:
    #  * örneklemde yeni: incelenen makale örneğinde hastalıkla hiç anılmamış (yöntemin "görmeden bulduğu" küme;
    #    bağımsız doğrulama bu kümede ölçülür: kaçı ClinicalTrials.gov'da kayıtlı?)
    #  * denenmemiş hipotez: ayrıca PubMed tam sayımında da anılmamış ve klinik çalışma kaydı yok
    new_rows = [r for _, r in drugs.iterrows() if r["status"] == "yeni" or (
        isinstance(r.get("durum"), str) and r["durum"] == "doğrudan kayıt yok (aday)")]
    checked = [r for r in new_rows if not _nan(r.get("clinical_trials"))]
    with_trials = [r for r in checked if int(r["clinical_trials"]) > 0]
    untested = [r for r in new_rows if (_nan(r.get("pubmed_direct")) or int(r["pubmed_direct"]) == 0)
                and (_nan(r.get("clinical_trials")) or int(r["clinical_trials"]) == 0)]

    # rastgele beklenen oran: hastalık için çalışması olan ilaç / değerlendirilen ilaç evreni (ChEMBL sözlüğünde)
    first_trial = labels.get("first_trial_year") or {}
    universe = (bt.get("n_universe_total") or 0) if bt else 0
    base_rate = (len(first_trial) / universe) if universe else None
    observed_rate = (len(with_trials) / len(checked)) if checked else None
    enrichment = (observed_rate / base_rate) if (observed_rate is not None and base_rate) else None

    new_items = []
    for r in new_rows[:top_new]:
        links = evidence.get(r["chemical_id"], [])
        genes = []
        for l in links:
            if l.get("gene") and l["gene"] not in genes:
                genes.append(l["gene"])
        new_items.append({
            "rank": int(r["rank"]),
            "chemical": r["chemical"],
            "chembl_id": r.get("chembl_id") if isinstance(r.get("chembl_id"), str) else "",
            "max_phase": None if _nan(r.get("max_phase")) else float(r["max_phase"]),
            "score": float(r["score"]),
            "direction": r.get("direction"),
            "harm_signal": r.get("harm_signal"),
            "genes": genes[:5],
            "mechanism": r.get("mechanism") if isinstance(r.get("mechanism"), str) else "",
            "clinical_trials": None if _nan(r.get("clinical_trials")) else int(r["clinical_trials"]),
            "pubmed_direct": None if _nan(r.get("pubmed_direct")) else int(r["pubmed_direct"]),
            "genetic_target": r.get("genetic_target") if isinstance(r.get("genetic_target"), str) else "",
            "network_target": r.get("network_target") if isinstance(r.get("network_target"), str) else "",
            "justification": justify(r.to_dict(), links, disease_cfg.get("label", "hastalık")),
        })

    # hedef sınıfları: yeni adayların en güçlü köprü genine göre
    classes: dict = defaultdict(list)
    for r in new_rows[:60]:
        links = evidence.get(r["chemical_id"], [])
        if links:
            top = max(links, key=lambda l: l.get("contribution", 0))
            classes[top["gene"]].append(r["chemical"])
    target_classes = sorted(({"gene": g, "n": len(v), "drugs": v[:6]} for g, v in classes.items()), key=lambda x: -x["n"])[:10]

    bt_summary: Dict[str, Any] = {}
    if bt and bt.get("schemes"):
        for s, res in bt["schemes"].items():
            m = res.get("metrics")
            if m:
                used = "fused" if "fused" in m else "method"  # listede kullanılan sıralama
                bt_summary[s] = {"n_positives": res.get("n_positives"), "n_universe": res.get("n_universe"), "ranking": used,
                                 "method_at_10": m[used]["hits_at_10"], "frequency_at_10": m["frequency"]["hits_at_10"],
                                 "method_at_25": m[used]["hits_at_25"], "frequency_at_25": m["frequency"]["hits_at_25"],
                                 "base_at_10": m["method"]["hits_at_10"],
                                 "random_at_10": (res.get("random_expected_hits") or {}).get("at_10"),
                                 "auc_method": m[used]["auc"], "auc_frequency": m["frequency"]["auc"],
                                 "ap_method": m[used].get("ap"), "ap_frequency": m["frequency"].get("ap"),
                                 "ap_random": res.get("random_expected_ap")}
        bt_summary["cutoff_year"] = bt.get("cutoff_year")
        bt_summary["dropped_bridges"] = [d[1] for d in bt.get("dropped_bridges", [])][:12]

    top_overall = [{"rank": int(r["rank"]), "chemical": r["chemical"], "status": r["status"]} for _, r in
                   cand[~cand["kind"].isin(["endojen", "tanı/yardımcı"])].head(10).iterrows()]

    summary = {
        "disease": disease_cfg.get("key") or "",
        "label": disease_cfg.get("label", ""),
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "n_candidates": int(len(cand)),
        "counts": counts,
        "top_overall": top_overall,
        "n_new": len(new_rows),
        "n_untested": len(untested),
        "n_new_checked": len(checked),
        "n_new_with_trials": len(with_trials),
        "new_with_trials": [{"chemical": r["chemical"], "rank": int(r["rank"]), "clinical_trials": int(r["clinical_trials"])} for r in with_trials],
        "base_rate": round(base_rate, 4) if base_rate is not None else None,
        "observed_rate": round(observed_rate, 4) if observed_rate is not None else None,
        "enrichment": round(enrichment, 1) if enrichment is not None else None,
        "n_drugs_with_trial": len(first_trial),
        "new_candidates": new_items,
        "target_classes": target_classes,
        "backtest": bt_summary,
        "verdict": _verdict(len(checked), len(with_trials), enrichment, bt_summary),
        "confidence": confidence or {"grade": "ölçülemedi", "reasons": [], "numbers": {}},
        "fusion": cand["fusion"].iloc[0] if "fusion" in cand.columns and len(cand) else "base",
    }
    (results_dir / "discovery.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def _verdict(n_checked: int, n_hits: int, enrichment, bt: dict) -> str:
    parts = []
    if n_checked == 0:
        parts.append("Yenilik kontrolü çalışmadığı için bağımsız doğrulama yapılamadı.")
    elif n_hits == 0:
        parts.append(f"Yeni {n_checked} adayın hiçbiri ClinicalTrials.gov'da bu hastalık için kayıtlı değil; hepsi denenmemiş hipotez.")
    else:
        parts.append(f"Yeni {n_checked} adayın {n_hits}'i bu hastalık için bağımsız klinik çalışmaya girmiş"
                     + (f" (rastgele beklenenin {round(enrichment, 1)} katı)." if enrichment else "."))
    tr = bt.get("trials")
    if tr:
        if tr["method_at_10"] > tr["frequency_at_10"]:
            parts.append(f"Geriye dönük testte yöntem ilk 10'da sıklık sıralamasını geçiyor ({tr['method_at_10']} / {tr['frequency_at_10']}).")
        elif tr["method_at_10"] == tr["frequency_at_10"]:
            parts.append(f"Geriye dönük testte yöntem ve sıklık sıralaması başa baş ({tr['method_at_10']} / {tr['frequency_at_10']}).")
        else:
            parts.append(f"Geriye dönük testte yöntem ilk 10'da sıklık sıralamasının gerisinde ({tr['method_at_10']} / {tr['frequency_at_10']}); sıralamaya temkinli bakılmalı.")
    return " ".join(parts)


def load_summary(results_dir: Path) -> dict:
    path = results_dir / "discovery.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def compare_row(key: str, label: str, s: dict) -> dict:
    """Bir keşif özetinden karşılaştırma tablosu satırı."""
    tr = (s.get("backtest") or {}).get("trials") or {}
    return {
        "key": key, "label": label, "n_candidates": s.get("n_candidates"),
        "top3": [t["chemical"] for t in s.get("top_overall", [])[:3]],
        "n_new": s.get("n_new"), "n_new_checked": s.get("n_new_checked"), "n_new_with_trials": s.get("n_new_with_trials"),
        "enrichment": s.get("enrichment"),
        "trials_at_10": f"{tr.get('method_at_10')} / {tr.get('frequency_at_10')}" if tr else "–",
        "top_classes": [f"{c['gene']} ({c['n']})" for c in s.get("target_classes", [])[:3]],
        "confidence": (s.get("confidence") or {}).get("grade", "ölçülemedi"),
        "built_at": s.get("built_at"),
    }


def compare(results_root: Path, diseases: dict) -> list:
    """Hastalıklar arası karşılaştırma satırları (ana sayfa tablosu)."""
    rows = []
    for key, cfg in diseases.items():
        s = load_summary(results_root / key)
        if s:
            rows.append(compare_row(key, cfg.get("label", key), s))
    return rows
