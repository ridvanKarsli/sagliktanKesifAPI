from kesif_engine.demo_data import DISEASE, demo_drugs, make_corpus
from kesif_engine.ranking import NEG, POS, Rec, gene_disease_direction, rank_candidates, summarize_direction, top_b_genes


def _bg(n=8):
    return {"g2": [Rec(f"e{i}", 2020, {"g2": "GENEB"}, {}) for i in range(n)]}


def test_planted_drugs_rank_high_and_popular_ones_do_not():
    a, c = make_corpus()
    ranked, evidence = rank_candidates(a, c)
    top10 = list(ranked["chemical_id"].head(10))
    assert {"F1", "F2", "F3"} <= set(top10)
    assert not any(cid.startswith("P") for cid in ranked["chemical_id"].head(5))
    assert "F1" in evidence and len(evidence["F1"]) >= 2


def test_drug_dictionary_restricts_candidates_and_adds_mechanism():
    a, c = make_corpus()
    drugs, drug_map = demo_drugs()
    ranked, evidence = rank_candidates(a, c, drugs=drugs, drug_map=drug_map, disease_id=DISEASE)
    assert set(ranked["chembl_id"]) <= set(drugs)
    f1 = ranked[ranked["chemical_id"] == "F1"].iloc[0]
    assert f1["mech_score"] > 0 and f1["lit_score"] > 0
    assert abs(f1["score"] - (f1["lit_score"] + f1["mech_score"])) < 1e-3
    assert "inhibitor GENE1" in f1["mechanism"]
    assert any(l["source"] == "chembl" for l in evidence["F1"])


def test_direction_consistent_when_gene_up_and_drug_inhibits():
    a, c = make_corpus()
    drugs, drug_map = demo_drugs()
    ranked, _ = rank_candidates(a, c, drugs=drugs, drug_map=drug_map, disease_id=DISEASE)
    f1 = ranked[ranked["chemical_id"] == "F1"].iloc[0]
    assert f1["direction"] == "tutarlı"
    pop = ranked[ranked["chemical_id"].str.startswith("P")]
    assert (pop["direction"] == "bilinmiyor").all()


def test_direction_opposite_when_drug_activates_up_gene():
    a = [Rec(str(i), 2020, {"g1": "GENEA", "g2": "GENEB"}, {}, [[POS, "Gene", "g1", "Disease", "MESH:X"]]) for i in range(4)]
    c = {"g1": [Rec(f"c{i}", 2020, {"g1": "GENEA"}, {"d1": "drugx"}, [[POS, "Chemical", "d1", "Gene", "g1"]]) for i in range(8)]}
    c.update(_bg())
    ranked, ev = rank_candidates(a, c, min_articles=1, min_lift=1.5, disease_id="MESH:X")
    assert ranked.iloc[0]["direction"] == "ters"
    assert ev["d1"][0]["gene_direction"] == "up" and ev["d1"][0]["drug_effect"] == "activate"


def test_gene_disease_direction_respects_disease_id():
    recs = [Rec("1", 2020, {"g1": "A"}, {}, [[NEG, "Gene", "g1", "Disease", "MESH:OTHER"]])]
    assert gene_disease_direction(recs, ["g1"], "MESH:X") == {"g1": "unknown"}
    assert gene_disease_direction(recs, ["g1"], None) == {"g1": "down"}


def test_summarize_direction():
    assert summarize_direction(["consistent", "unknown"]) == "tutarlı"
    assert summarize_direction(["consistent", "opposite"]) == "karışık"
    assert summarize_direction(["opposite"]) == "ters"
    assert summarize_direction([]) == "bilinmiyor"


def test_stop_chemicals_are_excluded():
    a, c = make_corpus()
    ranked, _ = rank_candidates(a, c)
    assert "water" not in set(ranked["chemical"])


def test_min_links_filters_single_bridge_candidates():
    a, c = make_corpus()
    loose, _ = rank_candidates(a, c, min_links=1)
    strict, _ = rank_candidates(a, c, min_links=3)
    assert len(strict) < len(loose)
    assert (strict["n_bridges"] >= 3).all()


def test_mechanism_only_candidate_survives_min_links():
    a = [Rec(str(i), 2020, {"g1": "GENEA"}, {"zz": "other"}) for i in range(4)]
    c = {"g1": [Rec(f"c{i}", 2020, {"g1": "GENEA"}, {}) for i in range(5)]}
    drugs = {"CH1": {"chembl_id": "CH1", "name": "mechdrug", "max_phase": 4.0, "synonyms": [],
                     "mechanisms": [{"action_type": "ANTAGONIST", "moa": "x", "genes": ["GENEA"]}]}}
    ranked, _ = rank_candidates(a, c, drugs=drugs, drug_map={"zz": "CH1"}, min_articles=1, min_links=2)
    assert list(ranked["chemical"]) == ["mechdrug"]
    assert ranked.iloc[0]["lit_score"] == 0 and ranked.iloc[0]["mech_score"] > 0


def test_direct_counts_and_evidence_pmids():
    a = [
        Rec("1", 2020, {"g1": "GENEA", "g2": "GENEB"}, {"d1": "drugx"}),
        Rec("2", 2020, {"g1": "GENEA", "g2": "GENEB"}, {}),
        Rec("3", 2020, {"g1": "GENEA", "g2": "GENEB"}, {}),
    ]
    c = {"g1": [Rec(f"c{i}", 2020, {"g1": "GENEA"}, {"d1": "drugx"} if i < 4 else {}) for i in range(10)]}
    c.update(_bg(10))
    ranked, evidence = rank_candidates(a, c, min_links=1, min_bc=2, min_articles=1, min_lift=1.5)
    row = ranked[ranked["chemical_id"] == "d1"].iloc[0]
    assert row["n_direct_a"] == 1 and row["direct_pmids"] == "1"
    assert evidence["d1"][0]["gene"] == "GENEA"


def test_chemical_label_prefers_majority_full_name_over_abbreviation():
    a = [Rec(str(i), 2020, {"g1": "A", "g2": "B"}, {}) for i in range(4)]
    c = {"g1": [Rec(f"c{i}", 2020, {"g1": "A"}, {"d1": "MTX" if i == 0 else "Methotrexate"}) for i in range(8)]}
    c.update(_bg())
    ranked, _ = rank_candidates(a, c, min_links=1, min_bc=2, min_articles=1, min_lift=1.5)
    row = ranked.iloc[0]
    assert row["chemical"] == "methotrexate" and "mtx" in row["aliases"].split(";")


def test_biomarker_genes_are_not_used_as_bridges():
    a = [Rec(str(i), 2020, {"1401": "C-reactive protein", "7124": "TNF-alpha"}, {}) for i in range(5)]
    assert [g for g, _, _ in top_b_genes(a, min_articles=1)] == ["7124"]


def test_top_b_genes_respects_min_articles():
    a = [Rec(str(i), 2020, {"g1": "A"} if i < 5 else {"g2": "B"}, {}) for i in range(6)]
    assert [g for g, _, _ in top_b_genes(a, top_k=10, min_articles=3)] == ["g1"]


def test_empty_input_returns_empty_frame():
    ranked, evidence = rank_candidates([], {})
    assert ranked.empty and evidence == {}
