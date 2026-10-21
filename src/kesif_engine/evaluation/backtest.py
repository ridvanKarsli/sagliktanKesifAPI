"""Geriye dönük (zaman kesitli) doğrulama, ilaç uzayında, çoklu etiket ve çoklu baz çizgiyle.

Sıralama yalnızca kesit yılına kadarki makalelerle yapılır. Sonra üç ayrı "pozitif" tanımıyla ölçülür:

  mentions : kesitten sonra hastalık literatüründe ilk kez anılan ilaçlar (gürültülü; anestezik, ayırıcı tanı
             ilaçları da girer; yalnızca karşılaştırma için tutulur)
  therapy  : kesitten sonra, "drug therapy" alt başlıklı hastalık makalelerinde ilk kez görülen ilaçlar
  trials   : kesitten sonra hastalık için ilk klinik çalışmaya giren ilaçlar (ClinicalTrials.gov)

Her etiket için sıralamalar: method (mevcut yöntem puanı), her kanıt kanalı tek başına (lit_only, mech_only, direct,
recent, genetic, biology, network), kanalların sıra birleştirmesi (rrf), verilmişse öğrenen birleştirme (fused) ve
frequency (kesit öncesi anılma sayısı). Yöntem frequency'yi geçmiyorsa öngörü gücü iddia edilemez.

Ölçütler: AUC, ilk 10/25/50'deki isabet ve ortalama kesinlik (AP; az pozitifte isabet sayısından daha kararlı).
Not: genetik ve ağ kanalları kesit yılına göre dilimlenemez (bugünkü veridir); bu yüzden bu kanallarda ölçülen başarı
hafif iyimser olabilir. Ağ kanalında metin madenciliği kenarları zaten kullanılmaz (bkz. scoring/network.py).
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Optional

import pandas as pd

from kesif_engine.scoring import drugspace, fusion
from kesif_engine.scoring import features as feature_table
from kesif_engine.scoring.ranking import Params, is_stop, pick_label, rank_candidates


def _auc(scores: pd.Series, positives: set) -> float:
    ranks = scores.rank(method="average")
    pos = ranks[ranks.index.isin(positives)]
    n_pos = len(pos)
    n_neg = len(ranks) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    u = pos.sum() - n_pos * (n_pos + 1) / 2
    return float(u / (n_pos * n_neg))


def _split(records, cutoff: int):
    before = [r for r in records if r.year is not None and r.year <= cutoff]
    after = [r for r in records if r.year is not None and r.year > cutoff]
    return before, after


def _ordered(s: pd.Series) -> list:
    """Puana göre azalan, eşitlikte anahtara göre (kararlı) sıra."""
    return [k for k, _ in sorted(s.items(), key=lambda kv: (-kv[1], kv[0]))]


def average_precision(s: pd.Series, positives: set) -> float:
    """Ortalama kesinlik: her pozitifin bulunduğu sıradaki kesinliklerin ortalaması (puanı 0 olanlar sona)."""
    if not positives:
        return float("nan")
    hits, total = 0, 0.0
    for i, k in enumerate(_ordered(s), 1):
        if k in positives:
            hits += 1
            total += hits / i
    return total / len(positives)


def _evaluate(rankings: dict, universe: list, positives: set, name: dict, future_count: Counter, ks) -> dict:
    uset = set(universe)
    out: Dict[str, Any] = {"n_universe": len(universe), "n_positives": len(positives)}
    if not positives or len(positives) >= len(universe):
        out["note"] = "Değerlendirme için yeterli pozitif örnek yok."
        return out
    out["metrics"] = {}
    for key, s in rankings.items():
        s = s[s.index.isin(uset)]
        ordered = [c for c in _ordered(s) if s[c] > 0]
        m = {"auc": round(_auc(s, positives), 4), "ap": round(average_precision(s, positives), 4)}
        for k in ks:
            m[f"hits_at_{k}"] = len(set(ordered[:k]) & positives)
        out["metrics"][key] = m
    out["random_expected_ap"] = round(len(positives) / len(universe), 4)
    out["random_expected_hits"] = {f"at_{k}": round(min(k, len(universe)) * len(positives) / len(universe), 2) for k in ks}
    method = rankings["method"][rankings["method"].index.isin(uset)]
    freq = rankings["frequency"][rankings["frequency"].index.isin(uset)]
    m_rank = {c: i + 1 for i, c in enumerate(method.sort_values(ascending=False).index)}
    f_rank = {c: i + 1 for i, c in enumerate(freq.sort_values(ascending=False).index)}
    out["positives"] = sorted(
        (
            {
                "drug": name[c],
                "future_evidence": int(future_count.get(c, 0)),
                "method_rank": m_rank[c] if method[c] > 0 else None,
                "frequency_rank": f_rank[c],
            }
            for c in positives
        ),
        key=lambda x: (-x["future_evidence"], x["drug"]),
    )
    return out


TIME_SLICED = ["hastalık makaleleri", "köprü gen literatürü", "doğrudan anılma", "doğrulama etiketleri"]


def leakage_report(external: Optional[dict], gene_counts: Optional[dict], use_dict: bool) -> dict:
    """Kesit yılına göre dilimlenebilen ve dilimlenemeyen girdiler.

    Dilimlenemeyenler bugünün verisidir; ölçülen başarıyı hafif iyimser yapabilirler. Bu yüzden her sonuçla birlikte
    açıkça raporlanır ve benchmark bu kanalların katkısını ayrıca ölçer.
    """
    not_sliced = []
    if use_dict:
        not_sliced.append({"input": "ilaç sözlüğü", "detail": "mekanizma (hedef) kayıtları ve ilacın evrene girişi bugünkü en yüksek faza göre"})
    if gene_counts:
        not_sliced.append({"input": "gen arka plan sayıları", "detail": "köprü gen özgüllüğü bugünkü toplam literatürle hesaplanır"})
    ext = external or {}
    if ext.get("genetic"):
        not_sliced.append({"input": "genetik ilişki", "detail": "bugünkü genetik veri; bilinen ilaç ve literatür kanıtı dışlanmış"})
    if ext.get("network"):
        not_sliced.append({"input": "protein ağı", "detail": "bugünkü etkileşim ağı; metin madenciliği kenarları dışlanmış"})
    return {"time_sliced": TIME_SLICED, "not_time_sliced": not_sliced}


def backtest(
    a_records,
    c_by_gene: dict,
    cutoff: int,
    drugs: Optional[dict] = None,
    drug_map: Optional[dict] = None,
    gene_info: Optional[dict] = None,
    disease_id: Optional[str] = None,
    labels: Optional[dict] = None,
    params: Optional[Params] = None,
    min_future: int = 2,
    ks=(10, 25, 50),
    gene_counts: Optional[dict] = None,
    hereditary: bool = False,
    external: Optional[dict] = None,
    fusion_decision: Optional[dict] = None,
    return_features: bool = False,
) -> dict:
    """external: genetik/ağ kanıtı (scoring.features); fusion_decision: değerlendirilecek birleştirme kararı.
    return_features=True ise sonuçta '_features' (ilaç × kanal tablosu) ve '_labels' (şema → pozitif kümesi) döner;
    benchmark öğrenen modeli bunlarla eğitir. Bu alanlar JSON'a yazılmamalıdır."""
    drugs = drugs or {}
    drug_map = drug_map or {}
    labels = labels or {}
    use_dict = bool(drugs)
    a_before, a_after = _split(a_records, cutoff)
    c_before = {g: _split(recs, cutoff)[0] for g, recs in c_by_gene.items()}
    therapy_pmids = set(labels.get("therapy_pmids") or [])
    audit_out = set(labels.get("audit_excluded") or [])  # elle denetimde yanlış bulunan etiketler: ölçüme hiç girmez

    ranked, _ = rank_candidates(
        a_before, c_before, drugs=drugs, drug_map=drug_map, gene_info=gene_info, disease_id=disease_id, params=params,
        gene_counts=gene_counts, hereditary=hereditary,
    )

    # Evren ilaç bazındadır: aynı ilacın farklı kimlikleri (MeSH, CHEMBL metin eşleşmesi) tek anahtarda birleşir,
    # yoksa bir pozitif iki kez sayılır ve metrikler bozulur.
    def key_of(cid: str) -> str:
        return drug_map.get(cid, cid) if use_dict else cid

    votes: dict = {}
    seen: Counter = Counter()
    direct_before: set = set()
    for rec in a_before:
        for cid, lab in rec.chemicals.items():
            k = key_of(cid)
            votes.setdefault(k, Counter())[lab.lower()] += 1
            direct_before.add(k)
            seen[k] += 1
    c_union: dict = {}
    for recs in c_before.values():
        for rec in recs:
            c_union[rec.pmid] = rec
    for rec in c_union.values():
        for cid, lab in rec.chemicals.items():
            k = key_of(cid)
            votes.setdefault(k, Counter())[lab.lower()] += 1
            seen[k] += 1
    mentions_after: Counter = Counter()
    for rec in a_after:
        for cid, lab in rec.chemicals.items():
            k = key_of(cid)
            mentions_after[k] += 1
            votes.setdefault(k, Counter())[lab.lower()] += 1
    name = {}
    for k, v in votes.items():
        d = drugs.get(k) if use_dict else None
        name[k] = (d["name"].lower() if d and d.get("name") else pick_label(v))

    # Evren: ilaç sınıfındakiler (ilaç, besin/takviye). Endojen moleküller ve tanı ajanları ne aday ne de
    # pozitif sayılır; aksi halde 'anjiyotensin', 'metakolin' gibi etiketler testi anlamsızlaştırır.
    gene_names = drugspace.gene_product_names(list(a_before) + list(c_union.values()))
    kind = {}
    for k, v in votes.items():
        d = drugs.get(k, {}) if use_dict else {}
        kind[k] = drugspace.classify(d, pick_label(v), gene_names)

    def in_space(k: str) -> bool:
        if is_stop(name.get(k, "")):
            return False
        if not drugspace.in_backtest_universe(kind.get(k, drugspace.KIND_DRUG)):
            return False
        return (k in drugs) if use_dict else True

    base_universe = [k for k in seen if in_space(k)]

    feats = feature_table.build(ranked, a_before, list(c_union.values()), drugs, drug_map, external,
                                universe=base_universe, reference_year=cutoff)
    channels = feature_table.available_channels(feats)
    rankings = {
        "method": feats["base"],
        "lit_only": feats["lit"],
        "mech_only": feats["mech"],
        "direct": feats["direct"],
        "recent": feats["recent"],
        "frequency": pd.Series({c: float(seen[c]) for c in base_universe}),
        "ltc": feats["ltc"],
    }
    for ch in ("genetic", "biology", "network"):
        if ch in channels:
            rankings[ch] = feats[ch]
    rankings["rrf"] = fusion.rrf(feats, channels) * feats["penalty"]
    if fusion_decision and fusion_decision.get("method") in ("logistic", "rrf"):
        rankings["fused"] = fusion.fused_score(feats, fusion_decision)

    result = {
        "cutoff_year": cutoff,
        "drug_space": use_dict,
        "n_a_before": len(a_before),
        "n_a_after": len(a_after),
        "n_scored_by_method": int((rankings["method"] > 0).sum()),
        "n_universe_total": len(base_universe),
        "n_excluded_endogenous": sum(1 for k in seen if kind.get(k) == drugspace.KIND_ENDOGENOUS),
        "n_excluded_diagnostic": sum(1 for k in seen if kind.get(k) == drugspace.KIND_DIAGNOSTIC),
        "dropped_bridges": ranked.attrs.get("dropped_bridges", []) if hasattr(ranked, "attrs") else [],
        "channels": channels,
        "leakage": leakage_report(external, gene_counts, use_dict),
        "schemes": {},
    }
    labels_out: dict = {}

    # 1) mentions: kesit sonrası hastalık literatüründe ilk kez anılanlar
    universe = [c for c in base_universe if c not in direct_before]
    positives = {c for c, n in mentions_after.items() if n >= min_future and c in set(universe)}
    result["schemes"]["mentions"] = _evaluate(rankings, universe, positives, name, mentions_after, ks)
    labels_out["mentions"] = (universe, positives)

    # 2) therapy: "drug therapy" alt başlıklı makalelerde ilk kez görülenler
    therapy_pmids = set(labels.get("therapy_pmids") or [])
    if therapy_pmids:
        th_before: Counter = Counter()
        th_after: Counter = Counter()
        for rec in a_before:
            if rec.pmid in therapy_pmids:
                for cid in rec.chemicals:
                    th_before[key_of(cid)] += 1
        for rec in a_after:
            if rec.pmid in therapy_pmids:
                for cid in rec.chemicals:
                    th_after[key_of(cid)] += 1
        universe = [c for c in base_universe if th_before[c] == 0]
        uset = set(universe)
        positives = {c for c, n in th_after.items() if n >= min_future and c in uset}
        result["schemes"]["therapy"] = _evaluate(rankings, universe, positives, name, th_after, ks)
        result["schemes"]["therapy"]["n_therapy_articles_after"] = sum(1 for r in a_after if r.pmid in therapy_pmids)
        labels_out["therapy"] = (universe, positives)

    # 3) trials: kesitten sonra hastalık için ilk klinik çalışmaya girenler
    first_trial = labels.get("first_trial_year") or {}
    if first_trial and use_dict:
        trial_year = {chembl_id: int(year) for chembl_id, year in first_trial.items()}
        universe = [c for c in base_universe if trial_year.get(c, 9999) > cutoff and c not in audit_out]
        uset = set(universe)
        positives = {c for c, y in trial_year.items() if y > cutoff and c in uset}
        after_count = Counter({c: 1 for c in positives})
        result["schemes"]["trials"] = _evaluate(rankings, universe, positives, name, after_count, ks)
        result["schemes"]["trials"]["n_drugs_with_any_trial"] = len(trial_year)
        labels_out["trials"] = (universe, positives)

        # 4) trials_novel: en katı test. Kesit öncesi hastalık literatüründe HİÇ anılmamış ve kesitten sonra
        #    klinik çalışmaya girmiş ilaçlar (gerçek "keşif" ölçütü; örnek sayısı küçük olur).
        universe_n = [c for c in universe if c not in direct_before]
        positives_n = {c for c in positives if c not in direct_before}
        result["schemes"]["trials_novel"] = _evaluate(
            rankings, universe_n, positives_n, name, Counter({c: 1 for c in positives_n}), ks
        )
        result["schemes"]["trials_novel"]["positives_all"] = sorted(name[c] for c in positives_n)
        labels_out["trials_novel"] = (universe_n, positives_n)

    # 5) trials_late: faz 2 ve üstü çalışmayla ilk kez test edilenler (ikinci, daha katı etiket; etiket şeması 3)
    first_late = labels.get("first_late_trial_year") or {}
    if first_late and use_dict:
        late_year = {k: int(y) for k, y in first_late.items()}
        universe_l = [c for c in base_universe if late_year.get(c, 9999) > cutoff and c not in audit_out]
        positives_l = {c for c, y in late_year.items() if y > cutoff and c in set(universe_l)}
        result["schemes"]["trials_late"] = _evaluate(rankings, universe_l, positives_l, name, Counter({c: 1 for c in positives_l}), ks)
        labels_out["trials_late"] = (universe_l, positives_l)

    # geriye uyumluluk: ana metrikler = en güvenilir mevcut şema
    primary = "trials" if "trials" in result["schemes"] else ("therapy" if "therapy" in result["schemes"] else "mentions")
    result["primary_scheme"] = primary
    for key in ("n_universe", "n_positives", "metrics", "random_expected_hits", "positives", "note"):
        if key in result["schemes"][primary]:
            result[key] = result["schemes"][primary][key]
    result["n_future_in_universe"] = result.get("n_positives", 0)
    if return_features:
        feats = feats.copy()
        feats["frequency"] = rankings["frequency"].reindex(feats.index).fillna(0.0)
        feats["direct_before"] = [1.0 if c in direct_before else 0.0 for c in feats.index]
        feats["name"] = [name.get(c, c) for c in feats.index]
        result["_features"] = feats
        result["_labels"] = labels_out
    return result
