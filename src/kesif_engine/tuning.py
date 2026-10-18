"""Çapraz doğrulamalı parametre ayarı.

Parametreler kesit A'da ızgara aramasıyla seçilir, seçilen ayar kesit B'de (A'dan bağımsız) test edilir ve
varsayılan ayarla karşılaştırılır. B'de de kazanıyorsa "doğrulanmış" sayılır; aksi hâlde A'ya uydurma olasılığı
vardır ve uygulanmaz. Çıktı: results/<hastalık>/tuning.json
"""

from __future__ import annotations

import datetime
import itertools
import logging
import math
from typing import Optional

from .backtest import backtest
from .ranking import Params

log = logging.getLogger(__name__)

GRID = {
    "w_mech": [0.0, 1.0, 3.0],
    "min_lift": [2.0, 3.0],
    "ctx_floor": [1.0, 0.5, 0.25],
}
SCHEME_WEIGHTS = {"trials": 1.0, "trials_novel": 1.0, "therapy": 0.5}


def objective(result: dict) -> float:
    """Tek sayı: tedavi odaklı şemalarda ilk sıralardaki isabet + AUC. Şema yoksa 0 katkı."""
    total = 0.0
    for scheme, w in SCHEME_WEIGHTS.items():
        r = result.get("schemes", {}).get(scheme)
        if not r or "metrics" not in r:
            continue
        m = r["metrics"]["method"]
        auc = m["auc"] if not (isinstance(m["auc"], float) and math.isnan(m["auc"])) else 0.5
        total += w * (m["hits_at_10"] + 0.5 * m["hits_at_25"] + 0.25 * m["hits_at_50"] + 2 * auc)
    return round(total, 4)


def _summary(result: dict) -> dict:
    return {s: r.get("metrics", {}).get("method") for s, r in result.get("schemes", {}).items()}


def tune(corpus, base: Params, cutoff_a: int = 2016, cutoff_b: int = 2019, grid: Optional[dict] = None) -> dict:
    grid = grid or GRID
    keys = list(grid)
    kwargs = corpus.kwargs()
    rows = []
    log.info("Parametre ayarı: kesit A=%d üzerinde %d kombinasyon", cutoff_a, math.prod(len(v) for v in grid.values()))
    for combo in itertools.product(*(grid[k] for k in keys)):
        p = Params.from_dict({**base.to_dict(), **dict(zip(keys, combo))})
        res = backtest(corpus.a, corpus.c, cutoff_a, labels=corpus.labels, params=p, **kwargs)
        rows.append({"params": dict(zip(keys, combo)), "objective": objective(res), "summary": _summary(res)})
        log.info("  %s -> %.3f", dict(zip(keys, combo)), rows[-1]["objective"])
    rows.sort(key=lambda r: -r["objective"])
    best = rows[0]
    best_params = Params.from_dict({**base.to_dict(), **best["params"]})

    log.info("Doğrulama: kesit B=%d", cutoff_b)
    res_best_b = backtest(corpus.a, corpus.c, cutoff_b, labels=corpus.labels, params=best_params, **kwargs)
    res_base_b = backtest(corpus.a, corpus.c, cutoff_b, labels=corpus.labels, params=base, **kwargs)
    obj_best_b, obj_base_b = objective(res_best_b), objective(res_base_b)
    validated = obj_best_b > obj_base_b
    return {
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "cutoff_a": cutoff_a,
        "cutoff_b": cutoff_b,
        "grid": grid,
        "base_params": base.to_dict(),
        "best_params": best_params.to_dict(),
        "best_on_a": best,
        "top_on_a": rows[:8],
        "validation_on_b": {
            "best_objective": obj_best_b,
            "base_objective": obj_base_b,
            "best_summary": _summary(res_best_b),
            "base_summary": _summary(res_base_b),
            "validated": validated,
        },
        "verdict": (
            "Kesit A'da seçilen ayar kesit B'de de varsayılanı geçti; uygulanabilir."
            if validated
            else "Kesit A'da seçilen ayar kesit B'de varsayılanı geçemedi; A'ya uydurma olasılığı var, uygulanmadı."
        ),
    }
