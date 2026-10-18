from kesif_engine.scoring.ranking import NEG, POS, Rec, gene_disease_direction, known_therapies


def test_known_therapies_combines_relations_and_therapy_articles():
    D = "MESH:X"
    recs = [Rec(f"r{i}", 2015 + i, {}, {"MESH:C": "colchicine"}, [[NEG, "Chemical", "MESH:C", "Disease", D]]) for i in range(3)]
    # metinden bulunan biyolojik: ilişkisi yok ama tedavi makalelerinde anılıyor
    recs += [Rec(f"t{i}", 2020, {}, {"CHEMBL:CH_ANA": "anakinra"}) for i in range(4)]
    recs += [Rec("h1", 2021, {}, {"MESH:P": "pembrolizumab"}, [[POS, "Chemical", "MESH:P", "Disease", D], [POS, "Chemical", "MESH:P", "Disease", D]])]
    drugs = {"CH_COL": {"chembl_id": "CH_COL", "name": "COLCHICINE", "max_phase": 4.0}, "CH_ANA": {"chembl_id": "CH_ANA", "name": "ANAKINRA", "max_phase": 4.0},
             "CH_PEM": {"chembl_id": "CH_PEM", "name": "PEMBROLIZUMAB", "max_phase": 4.0}}
    drug_map = {"MESH:C": "CH_COL", "CHEMBL:CH_ANA": "CH_ANA", "MESH:P": "CH_PEM"}
    df = known_therapies(recs, D, drugs, drug_map, therapy_pmids=[f"t{i}" for i in range(4)])
    by = {r["chemical"]: r for _, r in df.iterrows()}
    assert by["colchicine"]["n_treat_rel"] == 3 and by["colchicine"]["first_year"] == 2015 and by["colchicine"]["last_year"] == 2017
    assert by["anakinra"]["n_therapy_articles"] == 4 and by["anakinra"]["n_treat_rel"] == 0
    assert by["pembrolizumab"]["harm_signal"] == "tetikleme sinyali"
    assert list(df["chemical"])[0] in ("colchicine", "anakinra")


def test_genetic_association_dominates_single_correlation():
    recs = [Rec(str(i), 2020, {"g": "USH2A"}, {}, [["Association", "Gene", "g", "Disease", "MESH:RP"]]) for i in range(20)]
    recs.append(Rec("x", 2020, {"g": "USH2A"}, {}, [[POS, "Gene", "g", "Disease", "MESH:RP"]]))
    assert gene_disease_direction(recs, ["g"], "MESH:RP") == {"g": "mutated"}
