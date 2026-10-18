from collections import Counter

from kesif_engine.extraction.textmatch import DrugTextIndex, enrich
from kesif_engine.scoring.ranking import NEG, POS, Params, Rec, context_set, harm_label, rank_candidates

DRUGS = [
    {"chembl_id": "CHEMBL1201572", "name": "ETANERCEPT", "max_phase": 4.0, "type": "Protein", "synonyms": ["Enbrel"], "mechanisms": []},
    {"chembl_id": "CHEMBL1201571", "name": "ANAKINRA", "max_phase": 4.0, "type": "Protein", "synonyms": ["Kineret"], "mechanisms": []},
    {"chembl_id": "CHEMBL25", "name": "ASPIRIN", "max_phase": 4.0, "type": "Small molecule", "synonyms": [], "mechanisms": []},
    {"chembl_id": "CHEMBLX", "name": "INTERFERON ALFA-2A", "max_phase": 4.0, "type": "Protein", "synonyms": ["Roferon-A"], "mechanisms": []},
]


def test_text_index_finds_biologics_only_with_word_boundaries():
    idx = DrugTextIndex(DRUGS, min_phase=1.0)
    found = idx.find("Etanercept versus placebo in Behçet's syndrome; aspirin was continued. Interferon alfa-2a rescue.")
    assert set(found) == {"CHEMBL1201572", "CHEMBLX"}  # aspirin küçük molekül: PubTator zaten etiketler
    assert idx.find("The kineret trial") == {"CHEMBL1201571": "kineret"}
    assert idx.find("enbrelx is not a word") == {}


def test_enrich_adds_chembl_ids_without_overwriting():
    idx = DrugTextIndex(DRUGS, min_phase=1.0)
    recs = [Rec("1", 2020, {}, {"MESH:D1": "colchicine"}, text="Etanercept in Behcet"), Rec("2", 2020, {}, {}, text="")]
    assert enrich(recs, idx) == 1
    assert recs[0].chemicals == {"MESH:D1": "colchicine", "CHEMBL:CHEMBL1201572": "etanercept"}


def test_harm_label_is_ratio_based():
    assert harm_label(Counter({NEG: 405, POS: 2})) == "tedavi yönü"
    assert harm_label(Counter({NEG: 15, POS: 3})) == "karışık"
    assert harm_label(Counter({POS: 2})) == "tetikleme sinyali"
    assert harm_label(Counter({POS: 1})) == "tek tetikleme raporu"
    assert harm_label(Counter()) == "yok"


def test_context_set_keeps_enriched_diseases_only():
    a = [Rec(str(i), 2020, {}, {}, diseases={"D_UVE": "uveitis", "D_INF": "inflammation"}) for i in range(20)]
    c = [Rec(f"c{i}", 2020, {}, {}, diseases={"D_INF": "inflammation", "D_CANCER": "cancer"}) for i in range(100)]
    s = context_set(a, c, "D_TARGET")
    assert "D_UVE" in s and "D_TARGET" in s
    assert "D_INF" not in s and "D_CANCER" not in s


def test_context_weight_downranks_off_context_bridges_and_merges_duplicate_ids():
    a = [Rec(str(i), 2020, {"g1": "GENEA", "g2": "GENEB"}, {}, diseases={"D_UVE": "uveitis"}) for i in range(10)]
    on = [Rec(f"on{i}", 2020, {"g1": "GENEA"}, {"MESH:X": "drugon"}, diseases={"D_UVE": "uveitis"}) for i in range(6)]
    off = [Rec(f"off{i}", 2020, {"g1": "GENEA"}, {"MESH:Y": "drugoff", "MESH:Y2": "drugoff-biosimilar"}, diseases={"D_CANCER": "cancer"}) for i in range(6)]
    bg = [Rec(f"bg{i}", 2020, {"g2": "GENEB"}, {}, diseases={"D_CANCER": "cancer"}) for i in range(20)]
    c = {"g1": on + off, "g2": bg}
    drugs = {"CH_ON": {"chembl_id": "CH_ON", "name": "DRUGON", "max_phase": 4.0, "mechanisms": []},
             "CH_OFF": {"chembl_id": "CH_OFF", "name": "DRUGOFF", "max_phase": 4.0, "mechanisms": []}}
    drug_map = {"MESH:X": "CH_ON", "MESH:Y": "CH_OFF", "MESH:Y2": "CH_OFF"}
    p = Params(min_articles=1, min_lift=1.2, ctx_floor=0.25)
    ranked, ev = rank_candidates(a, c, drugs=drugs, drug_map=drug_map, disease_id="D_T", params=p)
    assert list(ranked["chemical"]) == ["drugon", "drugoff"]  # bağlam dışı köprü aşağıda
    assert len(ranked) == 2  # iki kimlikli drugoff tek satır
    assert ev[ranked.iloc[0]["chemical_id"]][0]["context_fraction"] == 1.0
