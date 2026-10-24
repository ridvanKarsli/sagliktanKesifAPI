"""Hipotez başına kalibre olasılık: "bu ilaç 5 yıl içinde bu hastalıkta klinik çalışmaya girer mi?" (Faz 3).

Sıralama puanı bir olasılık değildir (model sınıf dengeli eğitilir; hastalıkların taban oranları farklıdır). Olasılık,
adayın hastalık içindeki SIRA konumundan öğrenilir: geçmiş kesitlerde (2012/2015/2018) sıralamanın k. sırasındaki
yeni adayların ne kadarı H yıl içinde klinik çalışmaya girmiş? Eşleme tekdüze (sıra iyileştikçe olasılık azalmaz) ve
izotonik regresyonla (PAV) kurulur; kesit sonrası en az H yıl gözlem olan kesitler kullanılır.

Dürüst ölçüm: her hastalığın olasılıkları, o hastalık dışarıda bırakılarak öğrenilen eşlemeyle hesaplanır (model puanları
zaten hastalık dışarıda bırakılarak üretilir). Ölçütler: Brier puanı ve log kaybı, sabit taban oranına göre; beceri
(1 − Brier_model / Brier_sabit) için hastalık × kesit grupları üzerinden bootstrap güven aralığı; güvenilirlik tablosu.

Seçilmiş biçim neden sıra tabanlı: 52 hastalıklık denemede model puanının logit'ine Platt ölçekleme uç değerlerde aşırı
güvenliydi (tahmin %27, gerçekleşen %11); sıra tabanlı izotonik eşleme her dilimde tutarlıydı (ilk 5 sıra: tahmin %7,9,
gerçekleşen %8,8).
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

HORIZON = 5
LEVEL = 0.90
N_BOOT = 2000
SEED = 7
BINS = (0.0, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 1.0)


def pav(x: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Artan izotonik regresyon. Dönen: (eşik x'ler, değerler); tahmin için `predict`."""
    order = np.argsort(x, kind="mergesort")
    xs, ys = np.asarray(x, dtype=float)[order], np.asarray(y, dtype=float)[order]
    sums: List[float] = []
    counts: List[float] = []
    right: List[float] = []
    for xi, yi in zip(xs, ys):
        sums.append(yi)
        counts.append(1.0)
        right.append(xi)
        while len(sums) > 1 and sums[-2] / counts[-2] > sums[-1] / counts[-1]:
            s, c, r = sums.pop(), counts.pop(), right.pop()
            sums[-1] += s
            counts[-1] += c
            right[-1] = r
    return np.array(right), np.array([s / c for s, c in zip(sums, counts)])


def predict(table: Tuple[np.ndarray, np.ndarray], x) -> np.ndarray:
    breaks, values = table
    idx = np.clip(np.searchsorted(breaks, np.asarray(x, dtype=float), side="left"), 0, len(values) - 1)
    return values[idx]


def _feature(rank) -> np.ndarray:
    return -np.log(np.asarray(rank, dtype=float))


def rank_rows(scores: pd.Series, positives_h: set) -> pd.DataFrame:
    """Bir grup (hastalık × kesit) için sıra ve H yıllık etiket."""
    order = scores.sort_values(ascending=False, kind="mergesort")
    return pd.DataFrame({"rank": np.arange(1, len(order) + 1), "y": [1 if k in positives_h else 0 for k in order.index]})


def fit(rows: pd.DataFrame) -> dict:
    """rows: sütunlar rank, y (tüm eğitim grupları birleşik). Kayda uygun eşleme tablosu."""
    breaks, values = pav(_feature(rows["rank"].to_numpy()), rows["y"].to_numpy())
    # sıra 1, 2, 3… biçiminde okunabilir tablo: her eşik bir sıra sınırına karşılık gelir
    max_rank = [int(round(math.exp(-b))) if b > -50 else 10 ** 9 for b in breaks]
    return {"horizon_years": HORIZON, "feature": "-log(sıra)", "breaks": [round(float(b), 6) for b in breaks],
            "values": [round(float(v), 6) for v in values], "rank_upper": max_rank, "n": int(len(rows)),
            "base_rate": round(float(rows["y"].mean()), 6)}


def probability(calib: dict, rank) -> np.ndarray:
    return predict((np.array(calib["breaks"]), np.array(calib["values"])), _feature(rank))


def _brier(p, y) -> float:
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def _logloss(p, y) -> float:
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def evaluate(groups: Sequence[dict], n_boot: int = N_BOOT) -> dict:
    """groups: [{"disease", "cutoff", "rows": DataFrame(rank, y)}]. Hastalık dışarıda bırakılarak eşleme → olasılık."""
    diseases = sorted({g["disease"] for g in groups})
    per_group = []
    pooled = []
    for d in diseases:
        train = pd.concat([g["rows"] for g in groups if g["disease"] != d], ignore_index=True)
        if not len(train) or train["y"].sum() == 0:
            continue
        cal = fit(train)
        for g in (g for g in groups if g["disease"] == d):
            r = g["rows"]
            p = probability(cal, r["rank"].to_numpy())
            p0 = np.full(len(r), cal["base_rate"])
            y = r["y"].to_numpy()
            per_group.append({"disease": d, "cutoff": g["cutoff"], "brier": _brier(p, y), "brier_base": _brier(p0, y),
                              "n": len(r), "positives": int(y.sum())})
            pooled.append(pd.DataFrame({"p": p, "p0": p0, "y": y, "rank": r["rank"].to_numpy()}))
    if not pooled:
        return {"ok": False, "note": "Kalibrasyon için yeterli grup yok."}
    P = pd.concat(pooled, ignore_index=True)
    b, b0 = _brier(P.p, P.y), _brier(P.p0, P.y)
    # beceri için grup bootstrap'ı: grupların Brier toplamları üzerinden (her grup kendi satır sayısıyla)
    G = pd.DataFrame(per_group)
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(G), size=(n_boot, len(G)))
    w = G["n"].to_numpy()
    num = (G["brier"].to_numpy() * w)[idx].sum(axis=1)
    den = (G["brier_base"].to_numpy() * w)[idx].sum(axis=1)
    skills = 1 - num / den
    a = (1 - LEVEL) / 2
    reliability = (P.groupby(pd.cut(P.p, list(BINS), include_lowest=True), observed=True)
                   .agg(n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean")).reset_index())
    by_rank = (P.groupby(pd.cut(P["rank"], [0, 5, 10, 20, 50, 100, 10 ** 9]), observed=True)
               .agg(n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean")).reset_index())
    return {
        "ok": bool(np.quantile(skills, a) > 0), "horizon_years": HORIZON, "n_groups": len(G), "n_rows": int(len(P)),
        "positives": int(P.y.sum()), "brier": round(b, 6), "brier_base": round(b0, 6),
        "skill": round(1 - b / b0, 4), "skill_ci": [round(float(np.quantile(skills, a)), 4), round(float(np.quantile(skills, 1 - a)), 4)],
        "logloss": round(_logloss(P.p, P.y), 4), "logloss_base": round(_logloss(P.p0, P.y), 4),
        "reliability": [{"bin": str(r["p"]), "n": int(r["n"]), "predicted": round(float(r["predicted"]), 4),
                         "observed": round(float(r["observed"]), 4)} for _, r in reliability.iterrows()],
        "by_rank": [{"ranks": str(r["rank"]), "n": int(r["n"]), "predicted": round(float(r["predicted"]), 4),
                     "observed": round(float(r["observed"]), 4)} for _, r in by_rank.iterrows()],
    }


def build_groups(groups: Sequence[dict], score: Callable[[dict], pd.Series], first_years: Dict[str, Dict[str, int]],
                 horizon: int = HORIZON, last_year: Optional[int] = None) -> List[dict]:
    """Benchmark gruplarından kalibrasyon grupları: kesitten sonra en az `horizon` yıl gözlem olan kesitler; etiket
    = ilk klinik çalışma (kesit, kesit + horizon] aralığında."""
    import datetime

    last_year = last_year or datetime.date.today().year
    out = []
    for g in groups:
        if g["cutoff"] + horizon > last_year:
            continue
        fy = first_years.get(g["disease"]) or {}
        pos_h = {k for k in g["positives"] if fy.get(k, 9999) <= g["cutoff"] + horizon}
        s = score(g)
        if s is None or not len(s):
            continue
        out.append({"disease": g["disease"], "cutoff": g["cutoff"], "rows": rank_rows(s, pos_h)})
    return out
