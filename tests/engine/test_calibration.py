"""Kalibre olasılık: izotonik eşleme, hastalık dışarıda bırakılarak değerlendirme."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kesif_engine.evaluation import calibration


def _group(d, cut, n=200, seed=0):
    rng = np.random.default_rng(seed)
    rank = np.arange(1, n + 1)
    p = 0.3 / np.sqrt(rank)                     # üst sıralarda daha yüksek gerçek olasılık
    return {"disease": d, "cutoff": cut, "rows": pd.DataFrame({"rank": rank, "y": (rng.random(n) < p).astype(int)})}


def test_pav_monotone():
    br, vals = calibration.pav(np.array([1, 2, 3, 4, 5.0]), np.array([0, 1, 0, 1, 1]))
    assert np.all(np.diff(vals) >= 0)
    assert calibration.predict((br, vals), [0, 10]).tolist() == [vals[0], vals[-1]]


def test_fit_probability_decreases_with_rank_and_evaluate_beats_constant():
    groups = [_group(f"d{i}", c, seed=i * 10 + c) for i in range(6) for c in (2012, 2015)]
    cal = calibration.fit(pd.concat([g["rows"] for g in groups]))
    p = calibration.probability(cal, [1, 5, 50, 200])
    assert all(a >= b for a, b in zip(p, p[1:]))
    ev = calibration.evaluate(groups, n_boot=200)
    assert ev["brier"] < ev["brier_base"] and ev["skill"] > 0 and ev["by_rank"]


def test_build_groups_horizon():
    g = {"disease": "d", "cutoff": 2018, "positives": {"a", "b"}, "features": None}
    out = calibration.build_groups([g], lambda g: pd.Series({"a": 0.9, "b": 0.5, "c": 0.1}), {"d": {"a": 2020, "b": 2025}},
                                   horizon=5, last_year=2026)
    assert out[0]["rows"]["y"].tolist() == [1, 0, 0]          # b 2025'te: 5 yıl penceresinin dışında
    assert calibration.build_groups([{**g, "cutoff": 2023}], lambda g: pd.Series({"a": 1.0}), {}, 5, 2026) == []
