from kesif_engine.backtest import backtest
from kesif_engine.demo_data import DISEASE, demo_drugs, make_corpus
from kesif_engine.ranking import rank_candidates


def test_backtest_in_drug_space_reports_all_baselines():
    a, c = make_corpus()
    drugs, drug_map = demo_drugs()
    r = backtest(a, c, 2015, drugs=drugs, drug_map=drug_map, disease_id=DISEASE)
    assert r["drug_space"] is True
    assert set(r["metrics"]) == {"method", "lit_only", "mech_only", "frequency"}
    assert r["n_future_in_universe"] >= 3
    assert r["metrics"]["method"]["hits_at_10"] >= 3
    assert r["metrics"]["method"]["hits_at_10"] > 3 * r["random_expected_hits"]["at_10"]
    assert r["metrics"]["method"]["auc"] > r["metrics"]["frequency"]["auc"]
    names = {p["drug"] for p in r["positives"]}
    assert {"futuredrug1", "futuredrug2", "futuredrug3"} <= names
    assert all(0 <= m["auc"] <= 1 for m in r["metrics"].values())


def test_backtest_without_dictionary_still_works():
    a, c = make_corpus()
    r = backtest(a, c, 2015)
    assert r["drug_space"] is False and "metrics" in r


def test_planted_drugs_are_top_ranked_using_only_pre_cutoff_data():
    a, c = make_corpus()
    a_before = [r for r in a if r.year <= 2015]
    c_before = {g: [r for r in recs if r.year <= 2015] for g, recs in c.items()}
    ranked, _ = rank_candidates(a_before, c_before)
    assert {"F1", "F2", "F3"} <= set(ranked["chemical_id"].head(5))


def test_backtest_with_too_early_cutoff_does_not_crash():
    a, c = make_corpus()
    r = backtest(a, c, 1990)
    assert "note" in r or r["n_a_before"] == 0
