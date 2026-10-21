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
SECONDARY_SCHEME = "trials_late"
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
    Varsa ikinci, daha katı etiketin (faz 2+ çalışmalar) grupları da secondary=True ile eklenir; karar birincil etikette
    verilir, ikincil etikette en azından gerilememe aranır.
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
            for scheme, secondary in ((chosen, False), (SECONDARY_SCHEME, True)):
                if secondary and scheme == chosen:
                    continue
                sch = r["schemes"].get(scheme, {})
                if "metrics" not in sch:
                    continue
                universe, positives = r["_labels"][scheme]
                groups.append({"disease": disease, "cutoff": cut, "scheme": scheme, "secondary": secondary,
                               "features": r["_features"].loc[universe], "positives": set(positives),
                               "random_ap": sch.get("random_expected_ap"), "frequency_ap": sch["metrics"]["frequency"]["ap"],
                               "channels": r.get("channels", [])})
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
    all_groups = collect_groups(inputs, cutoffs, progress=progress)
    groups = [g for g in all_groups if not g.get("secondary")]
    hold = {d for d in hold_out if d in {g["disease"] for g in groups}}
    fit_groups = [g for g in groups if g["disease"] not in hold]
    fit_secondary = [g for g in all_groups if g.get("secondary") and g["disease"] not in hold]
    fit_diseases = sorted({g["disease"] for g in fit_groups})
    has_external = any(set(g["channels"]) & {"genetic", "biology", "network"} for g in fit_groups)
    methods = ["base", "rrf_core"] + (["rrf_all"] if has_external else [])
    can_learn = len(fit_diseases) >= MIN_DISEASES
    if can_learn:
        methods.append("logistic")
    names = [n for n in LOGISTIC_FEATURES if n in ("lit", "mech", "direct", "recent", "prevalence") or has_external]

    models: Dict[str, Optional[fusion.LogisticFusion]] = {}
    for d in fit_diseases:
        models[d] = None
        if can_learn:
            train = [g for g in fit_groups if g["disease"] != d]
            try:
                models[d] = fusion.LogisticFusion(names).fit([g["features"] for g in train], [_labels(g) for g in train])
            except ValueError:
                models[d] = None
    rows = _score_rows(fit_groups, methods, models)
    rows2 = _score_rows(fit_secondary, methods, models)
    scorecard = _scorecard(rows, methods, n_boot)
    scorecard_secondary = _scorecard(rows2, methods, n_boot) if len(rows2) >= MIN_GROUPS else {}

    decision = _decide(scorecard, methods, rows, fit_diseases, fit_groups, names, has_external, scorecard_secondary)
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
        "secondary_scheme": SECONDARY_SCHEME if scorecard_secondary else None,
        "scorecard_secondary": scorecard_secondary,
        "decision": decision,
        "gate_1": gate_1(rows, decision, inputs, fit_diseases, n_boot),
        "diseases": diseases_out,
    }


BASELINES = ("frequency", "ltc", "network")


def _score_rows(groups: List[dict], methods: List[str], models: Dict[str, Optional[fusion.LogisticFusion]]) -> List[dict]:
    rows = []
    for g in groups:
        f = g["features"]
        model = models.get(g["disease"])
        row = {"disease": g["disease"], "cutoff": g["cutoff"], "scheme": g["scheme"], "n_universe": len(f),
               "n_positives": len(g["positives"]), "random_ap": g["random_ap"], "frequency_ap": g["frequency_ap"],
               "ltc_ap": round(average_precision(f["ltc"], g["positives"]), 4) if "ltc" in f.columns else None}
        if "network" in f.columns and (f["network"] > 0).any():
            row["network_ap"] = round(average_precision(f["network"], g["positives"]), 4)
        for m in methods:
            if m == "logistic" and model is None:
                continue
            s = _score(m, f, model)
            row[f"{m}_ap"] = round(average_precision(s, g["positives"]), 4)
            row[f"{m}_h25"] = _hits(s, g["positives"], 25)
        rows.append(row)
    return rows


def _scorecard(rows: List[dict], methods: List[str], n_boot: int) -> Dict[str, Dict[str, Any]]:
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
    for b in BASELINES:
        vals = [float(r[f"{b}_ap"]) for r in rows if r.get(f"{b}_ap") is not None]
        if vals:
            scorecard[b] = {"ap": bootstrap_mean_ci(vals, n_boot)}
    return scorecard


GATE1_MIN_DISEASES = 25
GATE1_MAX_LABEL_ERROR = 0.05


def gate_1(rows: List[dict], decision: dict, inputs: Dict[str, dict], fit_diseases: List[str], n_boot: int = N_BOOT) -> dict:
    """Faz 1 kapısı: yeterli hastalık, denetlenmiş etiket ve kullanılan yöntemin tüm taban çizgilerini geçmesi.

    Etiket hatası, elle denetlenen örneklerden (data/<hastalık>/label_audit.json) hesaplanır; denetim yoksa kapı
    açılmaz. Taban çizgileri: sıklık, bağlantı terimi sayısı (LTC) ve varsa tek başına protein ağı yakınlığı.
    """
    used = decision.get("measured_as") or "base"
    checks: Dict[str, Any] = {}
    def _audit(d: str) -> dict:
        inp = inputs.get(d, {})
        a = inp.get("audit") or {}
        schema = (inp.get("labels") or {}).get("schema")
        # eski kayıtlar (şema alanı yok) ve etiket şeması değişmiş denetimler kapıya sayılmaz
        return a if ("labels_schema" not in a and schema is None) or a.get("labels_schema") == schema else {}

    n_checked = sum(int(_audit(d).get("n_checked", 0)) for d in fit_diseases)
    n_wrong = sum(int(_audit(d).get("n_wrong", 0)) for d in fit_diseases)
    audited = [d for d in fit_diseases if _audit(d).get("n_checked")]
    error = (n_wrong / n_checked) if n_checked else None
    checks["diseases"] = {"value": len(fit_diseases), "target": GATE1_MIN_DISEASES, "ok": len(fit_diseases) >= GATE1_MIN_DISEASES}
    checks["label_error"] = {"value": round(error, 4) if error is not None else None, "n_checked": n_checked,
                             "audited_diseases": len(audited), "target": GATE1_MAX_LABEL_ERROR,
                             "ok": error is not None and error < GATE1_MAX_LABEL_ERROR and len(audited) == len(fit_diseases)}
    beats = {}
    for b in BASELINES:
        diffs = [r[f"{used}_ap"] - r[f"{b}_ap"] for r in rows if r.get(f"{b}_ap") is not None and r.get(f"{used}_ap") is not None]
        if diffs:
            ci = bootstrap_mean_ci(diffs, n_boot)
            beats[b] = {**ci, "ok": ci["lo"] is not None and ci["lo"] > 0}
    checks["beats_baselines"] = {"method": used, "baselines": beats, "ok": bool(beats) and all(v["ok"] for v in beats.values())}
    passed = all(c["ok"] for c in checks.values())
    missing = [k for k, c in checks.items() if not c["ok"]]
    return {"passed": passed, "checks": checks, "missing": missing}


def _mean(vals) -> Optional[float]:
    v = [x for x in vals if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return round(sum(v) / len(v), 4) if v else None


def _decide(scorecard: dict, methods: List[str], rows: List[dict], fit_diseases: List[str], fit_groups: List[dict],
            names: List[str], has_external: bool, secondary: Optional[dict] = None) -> dict:
    base = {"method": "base", "measured_as": "base"}
    if len(fit_diseases) < MIN_DISEASES or len(rows) < MIN_GROUPS:
        return {**base, "verdict": "ölçülemedi",
                "reason": f"En az {MIN_DISEASES} hastalık ve {MIN_GROUPS} ölçülebilir grup gerekir "
                          f"(şu an {len(fit_diseases)} hastalık, {len(rows)} grup). Mevcut yöntem korunuyor."}
    ranked = sorted((m for m in methods if m != "base" and scorecard[m]["ap"]["mean"] is not None),
                    key=lambda m: -scorecard[m]["ap"]["mean"])
    for m in ranked:
        vs = scorecard[m]["vs_base"]
        sec = (secondary or {}).get(m, {}).get("vs_base")
        if sec and sec["mean"] is not None and sec["mean"] < 0:
            continue  # ikinci (faz 2+) etikette geriliyor: kazanç etikete özgü olabilir
        if vs["lo"] is not None and vs["lo"] > 0:
            out = {"measured_as": m, "verdict": "benimsendi",
                   "reason": f"{m}: {len(rows)} grupta ortalama AP {scorecard[m]['ap']['mean']} "
                             f"(mevcut yöntem {scorecard['base']['ap']['mean']}); fark %90 GA [{vs['lo']}, {vs['hi']}], "
                             f"{scorecard[m]['wins']} grupta önde, {scorecard[m]['losses']} grupta geride."
                             + (f" Faz 2+ etiketinde fark {sec['mean']:+.4f}." if sec and sec["mean"] is not None else "")}
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
