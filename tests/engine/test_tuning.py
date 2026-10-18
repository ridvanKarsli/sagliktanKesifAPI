from kesif_engine.demo_data import DISEASE, demo_drugs, make_corpus
from kesif_engine.ranking import NEG, POS, Params, Rec, gene_disease_direction, propagate_direction, rank_candidates
from kesif_engine.tuning import objective, tune


class _Corpus:
    def __init__(self):
        self.a, self.c = make_corpus()
        self.drugs, self.drug_map = demo_drugs()
        self.labels = {}

    def kwargs(self):
        return {"drugs": self.drugs, "drug_map": self.drug_map, "gene_info": {}, "disease_id": DISEASE}


def test_propagate_direction_from_known_neighbors():
    recs = [
        Rec("1", 2020, {"g1": "A", "g2": "B"}, {}, [[POS, "Gene", "g1", "Disease", "MESH:X"], [POS, "Gene", "g1", "Gene", "g2"]]),
        Rec("2", 2020, {"g1": "A", "g2": "B", "g3": "C"}, {}, [[POS, "Gene", "g2", "Gene", "g1"], [NEG, "Gene", "g3", "Gene", "g1"], [NEG, "Gene", "g1", "Gene", "g3"]]),
    ]
    d = gene_disease_direction(recs, ["g1", "g2", "g3", "g4"], "MESH:X")
    assert d == {"g1": "up", "g2": "unknown", "g3": "unknown", "g4": "unknown"}
    src = propagate_direction(recs, d)
    assert d["g2"] == "up" and src["g2"] == "propagated"       # pozitif ilişki: aynı yön
    assert d["g3"] == "down" and src["g3"] == "propagated"     # negatif ilişki: ters yön
    assert d["g4"] == "unknown" and src["g4"] == "unknown"
    assert src["g1"] == "relations"


def test_rank_marks_propagated_direction_in_evidence():
    a, c = make_corpus()
    drugs, drug_map = demo_drugs()
    _, ev = rank_candidates(a, c, drugs=drugs, drug_map=drug_map, disease_id=DISEASE, params=Params(propagate_direction=True))
    assert all("gene_direction_source" in l for links in ev.values() for l in links)


def test_tune_selects_on_a_and_validates_on_b():
    corpus = _Corpus()
    res = tune(corpus, Params(), cutoff_a=2013, cutoff_b=2016, grid={"w_mech": [0.0, 1.0], "min_lift": [2.0]})
    assert res["cutoff_a"] == 2013 and res["cutoff_b"] == 2016
    assert len(res["top_on_a"]) == 2 and "objective" in res["top_on_a"][0]
    assert set(res["best_params"]) == set(Params().to_dict())
    v = res["validation_on_b"]
    assert isinstance(v["validated"], bool) and "best_summary" in v and "base_summary" in v
    assert res["verdict"]


def test_objective_handles_missing_schemes():
    assert objective({"schemes": {}}) == 0.0
    assert objective({"schemes": {"trials": {"metrics": {"method": {"auc": 0.8, "hits_at_10": 2, "hits_at_25": 3, "hits_at_50": 4}}}}}) == 2 + 1.5 + 1 + 1.6
