"""Yöntem karnesi: karar kuralı, hold-out ayrımı ve güven notu."""

from __future__ import annotations

from kesif_engine.evaluation import benchmark
from kesif_engine.insights import confidence
from tests.engine.demo_data import DISEASE, demo_drugs, make_corpus


def _inputs(n: int):
    drugs, drug_map = demo_drugs()
    out = {}
    for i in range(n):
        a, c = make_corpus(seed=7 + i)
        first_trial = {"CHEMBLF1": 2019, "CHEMBLF2": 2020, "CHEMBLF3": 2021, "CHEMBLK1": 2010}
        out[f"h{i}"] = {"a": a, "c": c, "labels": {"first_trial_year": first_trial},
                        "kwargs": {"drugs": drugs, "drug_map": drug_map, "disease_id": DISEASE}}
    return out


def test_bootstrap_ci_brackets_mean_and_is_reproducible():
    vals = [0.1, 0.2, 0.3, 0.25, 0.15]
    a = benchmark.bootstrap_mean_ci(vals)
    assert a == benchmark.bootstrap_mean_ci(vals)
    assert a["lo"] <= a["mean"] <= a["hi"] and a["n"] == 5


def test_single_disease_cannot_change_the_method():
    r = benchmark.run(_inputs(1), cutoffs=(2016, 2018), n_boot=200)
    assert r["decision"]["verdict"] == "ölçülemedi"
    assert r["decision"]["method"] == "base"
    assert "logistic" not in r["methods"]  # tek hastalıkta dışarıda bırakarak öğrenme yapılamaz


def test_multi_disease_run_reports_scorecard_and_keeps_hold_out_separate():
    r = benchmark.run(_inputs(3), hold_out=["h2"], cutoffs=(2016, 2018), n_boot=200)
    assert r["fit_diseases"] == ["h0", "h1"] and r["hold_out"] == ["h2"]
    assert {"base", "rrf_core", "logistic"} <= set(r["methods"])
    assert all(g["disease"] != "h2" for d in r["fit_diseases"] for g in r["diseases"][d]["groups"])
    assert r["diseases"]["h2"]["groups"] == []  # hold-out karara katılmaz
    assert r["decision"]["verdict"] in ("benimsendi", "korundu")
    sc = r["scorecard"]
    assert sc["base"]["ap"]["mean"] > sc["random"]["ap"]["mean"]
    if r["decision"]["verdict"] == "benimsendi":
        assert sc[r["decision"]["measured_as"]]["vs_base"]["lo"] > 0


def test_confidence_grades_follow_measured_numbers():
    def bt(ap, freq, rnd, n_pos, h25):
        return {"primary_scheme": "trials", "cutoff_year": 2018, "schemes": {"trials": {
            "n_positives": n_pos, "random_expected_ap": rnd,
            "metrics": {"method": {"ap": ap, "hits_at_25": h25}, "frequency": {"ap": freq}}}}}

    assert confidence.grade(bt(0.30, 0.12, 0.01, 10, 4))["grade"] == confidence.HIGH
    assert confidence.grade(bt(0.03, 0.02, 0.01, 10, 1))["grade"] == confidence.MEDIUM
    low = confidence.grade(bt(0.02, 0.01, 0.006, 6, 0))
    assert low["grade"] == confidence.LOW and any("ilk 25" in r for r in low["reasons"])
    assert confidence.grade(bt(0.5, 0.1, 0.01, 2, 2))["grade"] == confidence.UNKNOWN
    assert confidence.grade(None)["grade"] == confidence.UNKNOWN
    small = confidence.grade(bt(0.30, 0.12, 0.01, 10, 4), n_articles=200)
    assert small["grade"] == confidence.MEDIUM  # küçük literatür yüksek notu sınırlar
