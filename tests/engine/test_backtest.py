from kesif_engine.evaluation.backtest import backtest
from kesif_engine.scoring.ranking import rank_candidates
from tests.engine.demo_data import DISEASE, demo_drugs, make_corpus


def test_backtest_in_drug_space_reports_all_baselines():
    a, c = make_corpus()
    drugs, drug_map = demo_drugs()
    r = backtest(a, c, 2015, drugs=drugs, drug_map=drug_map, disease_id=DISEASE)
    assert r["drug_space"] is True
    assert {"method", "lit_only", "mech_only", "frequency", "direct", "recent", "rrf"} <= set(r["metrics"])
    assert "genetic" not in r["metrics"]  # dış kanıt verilmedi: kanal ölçülmez
    assert all(0 <= m["ap"] <= 1 for m in r["metrics"].values())
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


def test_backtest_reports_external_channels_and_fused_ranking():
    """Genetik/ağ kanıtı verildiğinde kanallar ayrı ölçülür; birleştirme kararı verilirse 'fused' sıralaması eklenir."""
    a, c = make_corpus()
    drugs, drug_map = demo_drugs()
    targets = sorted({g for d in drugs.values() for m in d.get("mechanisms", []) for g in m.get("genes", [])})
    external = {"genetic": {targets[0]: {"genetic_association": 0.8}}, "network": {t: 2.5 for t in targets[:2]}}
    r = backtest(a, c, 2015, drugs=drugs, drug_map=drug_map, disease_id=DISEASE, external=external,
                 fusion_decision={"method": "rrf"}, return_features=True)
    assert {"genetic", "network", "fused"} <= set(r["metrics"])
    assert "genetic" in r["channels"] and "network" in r["channels"]
    feats = r["_features"]
    assert set(feats.columns) >= {"lit", "mech", "genetic", "network", "prevalence"}
    universe, positives = r["_labels"]["mentions"]
    assert positives <= set(universe) <= set(feats.index)


def test_average_precision_is_one_when_positives_are_on_top():
    import pandas as pd

    from kesif_engine.evaluation.backtest import average_precision

    s = pd.Series({"a": 3.0, "b": 2.0, "c": 1.0, "d": 0.0})
    assert average_precision(s, {"a", "b"}) == 1.0
    assert abs(average_precision(s, {"c"}) - 1 / 3) < 1e-9
