"""Yöntem karnesi: çok hastalık × çok kesit yılı ölçümü ve birleştirme kararının ölçümle verilmesi.

Tek hastalık, tek kesit yılı gürültülüdür: Behçet'te 2018 kesitinde iyi görünen bir ayar 2012'de kötü olabilir. Bu
modül her hastalığı birden fazla kesit yılında geriye dönük test eder ve yöntemleri aynı gruplar üzerinde eşleştirerek
karşılaştırır.

Karşılaştırılan sıralamalar:
    base       mevcut yöntem puanı (literatür köprüsü + mekanizma, zarar cezasıyla)
    rrf_core   literatür kanallarının sıra birleştirmesi (lit, mech, direct, recent)
    rrf_all    tüm mevcut kanallar (genetik, biyoloji, ağ dahil; varsa)
    logistic   öğrenen birleştirme; her hastalık için o hastalık EĞİTİMDEN ÇIKARILARAK ölçülür (leave-one-disease-out)

Karar kuralı (değişiklik ancak kanıtla):
    1. Ölçüm için en az 2 hastalık ve 4 grup (hastalık × kesit) gerekir; yoksa karar "ölçülemedi", mevcut ayar korunur.
    2. Yöntemler ortalama AP ile sıralanır (öğrenen model yalnızca eğitimde görmediği hastalıkta ölçülür).
    3. En iyisi, base'e karşı eşleştirilmiş farkın önyükleme (bootstrap) %90 güven aralığının alt sınırı 0'ın üstündeyse
       benimsenir; değilse base kalır.
    4. "hold_out" işaretli hastalıklar karara hiç katılmaz; seçilen yöntem en sonda bunlarda ayrıca raporlanır.

Bütün rastgelelik sabit tohumla yapılır; aynı girdi aynı kararı verir.
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Callable, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from kesif_engine.evaluation.backtest import average_precision, backtest
from kesif_engine.scoring import fusion

CUTOFFS = (2012, 2015, 2018)
SCHEME_PREFERENCE = ("trials", "therapy", "mentions")
CORE_CHANNELS = ["lit", "mech", "direct", "recent"]
LOGISTIC_FEATURES = ["lit", "mech", "direct", "recent", "genetic", "biology", "network", "prevalence"]
N_BOOT = 2000
SEED = 7
MIN_DISEASES = 2
MIN_GROUPS = 4


def _hits(s: pd.Series, positives: set, k: int) -> int:
    top = [i for i, _ in sorted(s.items(), key=lambda kv: (-kv[1], kv[0])) if s[i] > 0][:k]
    return len(set(top) & positives)


def bootstrap_mean_ci(values: Iterable[float], n_boot: int = N_BOOT, level: float = 0.90, seed: int = SEED) -> dict:
    v = np.array([x for x in values if x is not None and not math.isnan(x)], dtype=float)
    if len(v) == 0:
        return {"mean": None, "lo": None, "hi": None, "n": 0}
    rng = np.random.default_rng(seed)
    means = v[rng.integers(0, len(v), size=(n_boot, len(v)))].mean(axis=1)
    a = (1 - level) / 2
    return {"mean": round(float(v.mean()), 4), "lo": round(float(np.quantile(means, a)), 4),
            "hi": round(float(np.quantile(means, 1 - a)), 4), "n": int(len(v))}


def collect_groups(inputs: Dict[str, dict], cutoffs=CUTOFFS, scheme_preference=SCHEME_PREFERENCE,
                   progress: Optional[Callable[[str], None]] = None) -> List[dict]:
    """inputs: {hastalık: {"a", "c", "labels", "params", "kwargs", "external"}} → grup listesi.

    Her grup: hastalık, kesit, şema, özellik tablosu (yalnızca o şemanın evreni) ve pozitif kümesi.
    Her hastalık için şema tercih sırasına göre ilk ölçülebilir şema seçilir (aynı hastalıkta kesitler aynı şemayı kullanır).
    """
    groups = []
    for disease in sorted(inputs):
        inp = inputs[disease]
        chosen = None
        per_cut = []
        for cut in cutoffs:
            if progress:
                progress(f"{disease} — {cut} kesiti")
            r = backtest(inp["a"], inp["c"], cut, labels=inp.get("labels"), params=inp.get("params"),
                         external=inp.get("external"), return_features=True, **(inp.get("kwargs") or {}))
            per_cut.append((cut, r))
        for scheme in scheme_preference:
            if sum(1 for _, r in per_cut if "metrics" in r["schemes"].get(scheme, {})) >= 1:
                chosen = scheme
                break
        if not chosen:
            continue
        for cut, r in per_cut:
            sch = r["schemes"].get(chosen, {})
            if "metrics" not in sch:
                continue
            universe, positives = r["_labels"][chosen]
            groups.append({"disease": disease, "cutoff": cut, "scheme": chosen, "features": r["_features"].loc[universe],
                           "positives": set(positives), "random_ap": sch.get("random_expected_ap"),
                           "frequency_ap": sch["metrics"]["frequency"]["ap"], "channels": r.get("channels", [])})
    return groups


def _score(method: str, f: pd.DataFrame, model: Optional[fusion.LogisticFusion] = None) -> pd.Series:
    if method == "base":
        return f["base"]
    if method == "rrf_core":
        return fusion.rrf(f, CORE_CHANNELS) * f["penalty"]
    if method == "rrf_all":
        return fusion.rrf(f, fusion.available(f)) * f["penalty"]
    if method == "logistic" and model is not None:
        return model.predict(f) * f["penalty"]
    raise ValueError(method)


def _labels(g: dict) -> pd.Series:
    return pd.Series({k: 1.0 for k in g["positives"]}, dtype=float)


def run(inputs: Dict[str, dict], hold_out: Iterable[str] = (), cutoffs=CUTOFFS, n_boot: int = N_BOOT,
        progress: Optional[Callable[[str], None]] = None) -> dict:
    """Ölçümü yapar; {"decision": {...}, "scorecard": {...}, "diseases": {...}} döndürür (JSON'a uygun)."""
    groups = collect_groups(inputs, cutoffs, progress=progress)
    hold = {d for d in hold_out if d in {g["disease"] for g in groups}}
    fit_groups = [g for g in groups if g["disease"] not in hold]
    fit_diseases = sorted({g["disease"] for g in fit_groups})
    has_external = any(set(g["channels"]) & {"genetic", "biology", "network"} for g in fit_groups)
    methods = ["base", "rrf_core"] + (["rrf_all"] if has_external else [])
    can_learn = len(fit_diseases) >= MIN_DISEASES
    if can_learn:
        methods.append("logistic")
    names = [n for n in LOGISTIC_FEATURES if n in ("lit", "mech", "direct", "recent", "prevalence") or has_external]

    rows = []
    for d in fit_diseases:
        model = None
        if can_learn:
            train = [g for g in fit_groups if g["disease"] != d]
            try:
                model = fusion.LogisticFusion(names).fit([g["features"] for g in train], [_labels(g) for g in train])
            except ValueError:
                model = None
        for g in (x for x in fit_groups if x["disease"] == d):
            row = {"disease": d, "cutoff": g["cutoff"], "scheme": g["scheme"], "n_universe": len(g["features"]),
                   "n_positives": len(g["positives"]), "random_ap": g["random_ap"], "frequency_ap": g["frequency_ap"]}
            for m in methods:
                if m == "logistic" and model is None:
                    continue
                s = _score(m, g["features"], model)
                row[f"{m}_ap"] = round(average_precision(s, g["positives"]), 4)
                row[f"{m}_h25"] = _hits(s, g["positives"], 25)
            rows.append(row)

    scorecard: Dict[str, Dict[str, Any]] = {}
    for m in methods:
        vals = [float(r[f"{m}_ap"]) for r in rows if r.get(f"{m}_ap") is not None]
        h25 = [float(r[f"{m}_h25"]) for r in rows if f"{m}_h25" in r]
        scorecard[m] = {"ap": bootstrap_mean_ci(vals, n_boot), "hits_at_25": bootstrap_mean_ci(h25, n_boot)}
        if m != "base":
            diffs = [r[f"{m}_ap"] - r["base_ap"] for r in rows if f"{m}_ap" in r]
            scorecard[m]["vs_base"] = bootstrap_mean_ci(diffs, n_boot)
            scorecard[m]["wins"] = sum(1 for x in diffs if x > 0)
            scorecard[m]["losses"] = sum(1 for x in diffs if x < 0)
    scorecard["random"] = {"ap": bootstrap_mean_ci([r["random_ap"] for r in rows], n_boot)}
    scorecard["frequency"] = {"ap": bootstrap_mean_ci([r["frequency_ap"] for r in rows], n_boot)}

    decision = _decide(scorecard, methods, rows, fit_diseases, fit_groups, names, has_external)
    if hold and decision["method"] != "base":
        decision["hold_out"] = _evaluate_hold_out(decision, [g for g in groups if g["disease"] in hold])

    diseases_out: Dict[str, dict] = {}
    for d in sorted({g["disease"] for g in groups}):
        drows = [r for r in rows if r["disease"] == d]
        diseases_out[d] = {
            "hold_out": d in hold,
            "scheme": next((g["scheme"] for g in groups if g["disease"] == d), None),
            "groups": drows,
            "mean_base_ap": _mean([r.get("base_ap") for r in drows]),
            "mean_random_ap": _mean([r.get("random_ap") for r in drows]),
            "mean_frequency_ap": _mean([r.get("frequency_ap") for r in drows]),
            "mean_chosen_ap": _mean([r.get(f"{decision['measured_as']}_ap") for r in drows]) if decision.get("measured_as") else None,
            "mean_base_h25": _mean([r.get("base_h25") for r in drows]),
            "mean_chosen_h25": _mean([r.get(f"{decision['measured_as']}_h25") for r in drows]) if decision.get("measured_as") else None,
        }
    return {
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "cutoffs": list(cutoffs),
        "n_groups": len(rows),
        "fit_diseases": fit_diseases,
        "hold_out": sorted(hold),
        "methods": methods,
        "scorecard": scorecard,
        "decision": decision,
        "diseases": diseases_out,
    }


def _mean(vals) -> Optional[float]:
    v = [x for x in vals if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return round(sum(v) / len(v), 4) if v else None


def _decide(scorecard: dict, methods: List[str], rows: List[dict], fit_diseases: List[str], fit_groups: List[dict],
            names: List[str], has_external: bool) -> dict:
    base = {"method": "base", "measured_as": "base"}
    if len(fit_diseases) < MIN_DISEASES or len(rows) < MIN_GROUPS:
        return {**base, "verdict": "ölçülemedi",
                "reason": f"En az {MIN_DISEASES} hastalık ve {MIN_GROUPS} ölçülebilir grup gerekir "
                          f"(şu an {len(fit_diseases)} hastalık, {len(rows)} grup). Mevcut yöntem korunuyor."}
    ranked = sorted((m for m in methods if m != "base" and scorecard[m]["ap"]["mean"] is not None),
                    key=lambda m: -scorecard[m]["ap"]["mean"])
    for m in ranked:
        vs = scorecard[m]["vs_base"]
        if vs["lo"] is not None and vs["lo"] > 0:
            out = {"measured_as": m, "verdict": "benimsendi",
                   "reason": f"{m}: {len(rows)} grupta ortalama AP {scorecard[m]['ap']['mean']} "
                             f"(mevcut yöntem {scorecard['base']['ap']['mean']}); fark %90 GA [{vs['lo']}, {vs['hi']}], "
                             f"{scorecard[m]['wins']} grupta önde, {scorecard[m]['losses']} grupta geride."}
            if m == "logistic":
                model = fusion.LogisticFusion(names).fit([g["features"] for g in fit_groups], [_labels(g) for g in fit_groups])
                return {**out, "method": "logistic", "model": model.to_dict(), "weights": model.explain()}
            channels = CORE_CHANNELS if m == "rrf_core" else sorted({c for g in fit_groups for c in g["channels"]},
                                                                   key=list(fusion.CHANNELS).index)
            return {**out, "method": "rrf", "channels": channels}
    best = ranked[0] if ranked else None
    reason = "Hiçbir birleştirme mevcut yöntemi güvenilir biçimde geçemedi; mevcut yöntem korunuyor."
    if best:
        vs = scorecard[best]["vs_base"]
        reason += f" En iyi aday {best}: fark %90 GA [{vs['lo']}, {vs['hi']}]."
    return {**base, "verdict": "korundu", "reason": reason}


def _evaluate_hold_out(decision: dict, groups: List[dict]) -> dict:
    rows = []
    for g in groups:
        f = g["features"]
        chosen = fusion.fused_score(f, decision)
        rows.append({"disease": g["disease"], "cutoff": g["cutoff"],
                     "base_ap": round(average_precision(f["base"], g["positives"]), 4),
                     "chosen_ap": round(average_precision(chosen, g["positives"]), 4),
                     "random_ap": g["random_ap"]})
    diffs = [r["chosen_ap"] - r["base_ap"] for r in rows]
    return {"groups": rows, "mean_base_ap": _mean([r["base_ap"] for r in rows]),
            "mean_chosen_ap": _mean([r["chosen_ap"] for r in rows]),
            "confirmed": bool(diffs) and sum(diffs) > 0}
