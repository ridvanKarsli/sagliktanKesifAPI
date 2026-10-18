"""Sekiz hastalık karşılaştırmasından çıkan kalıcı düzeltmelerin testleri."""

from kesif_engine.evaluation.labels import map_trials_to_drugs
from kesif_engine.pipeline.corpus import merge_orthologs
from kesif_engine.scoring import drugspace
from kesif_engine.scoring.ranking import NEG, POS, Rec, _consistency, filter_specific, gene_disease_direction, rank_candidates, specificity
from kesif_engine.sources.clinicaltrials import parse_trials

D = "MESH:X"


def _bg(n=20):
    """Arka plan geni: lift hesabında ilaçsız gen literatürü (tek genli sentetik veride lift ~1 kalır)."""
    a = [Rec(f"bga{i}", 2020, {"bg": "BG"}, {}) for i in range(3)]
    c = [Rec(f"bgc{i}", 2020, {"bg": "BG"}, {f"MESH:O{i}": f"other{i}"}) for i in range(n)]
    return a, c


def _drug(name, phase=4.0, atc=None, typ="Small molecule", mechanisms=None, natural=0):
    return {"chembl_id": "CH_" + name.upper().replace(" ", "_"), "name": name.upper(), "max_phase": phase, "type": typ,
            "atc": atc or [], "mechanisms": mechanisms or [], "natural_product": natural, "synonyms": []}


# ---------- ilaç sınıfları ----------

def test_endogenous_mediators_are_classified_endogenous():
    for name in ("angiotensin II", "aldosterone", "endothelin-1", "nitric oxide", "norepinephrine", "insulin human"):
        assert drugspace.classify(_drug(name, phase=2.0), name) == drugspace.KIND_ENDOGENOUS, name


def test_gene_product_match_marks_drug_endogenous():
    genes = drugspace.gene_product_names([Rec("1", 2020, {"g1": "ghrelin", "g2": "C-peptide"}, {})])
    assert drugspace.classify(_drug("ghrelin", 3.0), "ghrelin", genes) == drugspace.KIND_ENDOGENOUS
    assert drugspace.classify(_drug("human c-peptide", 2.0), "c-peptide", genes) == drugspace.KIND_ENDOGENOUS
    # gerçek ilaç, gen adıyla çakışmıyor
    assert drugspace.classify(_drug("empagliflozin"), "empagliflozin", genes) == drugspace.KIND_DRUG


def test_diagnostic_agents_by_atc_and_name():
    assert drugspace.classify(_drug("methacholine", atc=["V04CX"]), "methacholine") == drugspace.KIND_DIAGNOSTIC
    assert drugspace.classify(_drug("iohexol", atc=["V08AB02"]), "iohexol") == drugspace.KIND_DIAGNOSTIC
    assert drugspace.classify(_drug("fluorescein"), "sodium fluorescein") == drugspace.KIND_DIAGNOSTIC
    # birden fazla ATC: yalnızca hepsi dışlanan sınıftaysa dışlanır (hyaluronik asit: S01K + M09)
    assert drugspace.classify(_drug("hyaluronic acid", atc=["S01KA01", "M09AX01"]), "hyaluronic acid") == drugspace.KIND_DRUG
    assert drugspace.classify(_drug("heparin", atc=["B01AB01"]), "heparin") == drugspace.KIND_DRUG


def test_nutrients_and_natural_products_are_tagged_not_dropped():
    assert drugspace.classify(_drug("curcumin", 3.0), "curcumin") == drugspace.KIND_NUTRIENT
    # natural_product bayrağı tek başına sınıf değiştirmez (sentetik ilaçlarda da 1 olabiliyor)
    assert drugspace.classify(_drug("omapatrilat", 2.0, natural=1), "omapatrilat") == drugspace.KIND_DRUG
    # onaylı, mekanizması bilinen doğal ürün (kolşisin) ilaçtır
    assert drugspace.classify(_drug("colchicine", 4.0, natural=1, mechanisms=[{"genes": ["TUBB"]}]), "colchicine") == drugspace.KIND_DRUG
    assert drugspace.in_backtest_universe(drugspace.KIND_NUTRIENT)
    assert not drugspace.in_backtest_universe(drugspace.KIND_ENDOGENOUS)


# ---------- köprü gen özgüllüğü ----------

def test_specificity_gate_drops_generic_gene():
    counts = {"_total": {"n": 1000}, "tnf": {"n": 100}, "mefv": {"n": 1}}
    # hastalıkta 10/100 makale: TNF payı 0.1 vs literatür 0.1 -> özgüllük 1 (elenir); MEFV 0.05 vs 0.001 -> 50
    bridges = [("tnf", "TNF", 10, "primary"), ("mefv", "MEFV", 5, "primary")]
    keep, dropped = filter_specific(bridges, 100, counts, 2.0)
    assert [b[0] for b in keep] == ["mefv"] and dropped[0][0] == "tnf"
    assert specificity(5, 100, counts, "mefv") == 50.0
    assert specificity(5, 100, counts, "unknown_gene") is None  # sayı yoksa eşik uygulanmaz
    assert filter_specific(bridges, 100, counts, 0)[0] == bridges  # 0 = kapalı
    # yolak komşusu: eşiğin üçte biri (0.67) -> özgüllük 1 olan TNF komşu olsaydı kalırdı
    keep2, _ = filter_specific([("tnf", "TNF", 10, "neighbor")], 100, counts, 2.0)
    assert len(keep2) == 1


def test_rank_uses_specificity_to_exclude_generic_bridge():
    bga, bgc = _bg()
    a = [Rec(f"a{i}", 2020, {"tnf": "TNF-alpha", "mefv": "MEFV"}, {}) for i in range(10)] + bga
    c = {"tnf": [Rec(f"t{i}", 2020, {"tnf": "TNF-alpha"}, {"MESH:INF": "infliximab"}) for i in range(5)],
         "mefv": [Rec(f"m{i}", 2020, {"mefv": "MEFV"}, {"MESH:COL": "colchicine"}) for i in range(5)], "bg": bgc}
    counts = {"_total": {"n": 1000}, "tnf": {"n": 900}, "mefv": {"n": 1}, "bg": {"n": 1}}
    ranked, _ = rank_candidates(a, c, min_articles=1, min_lift=1.2, gene_counts=counts, min_spec=2.0)
    assert "colchicine" in set(ranked["chemical"]) and "infliximab" not in set(ranked["chemical"])
    assert ranked.attrs["dropped_bridges"][0][1] == "TNF-alpha"


# ---------- güvenlik cezası ----------

def test_harm_penalty_demotes_disease_inducing_agent():
    bga, bgc = _bg()
    a = [Rec(f"a{i}", 2020, {"g": "G"}, {}) for i in range(6)] + bga
    a += [Rec(f"h{i}", 2020, {}, {"MESH:DOCA": "doca"}, [[POS, "Chemical", "MESH:DOCA", "Disease", D]]) for i in range(3)]
    c = {"g": [Rec(f"c{i}", 2020, {"g": "G"}, {"MESH:DOCA": "doca"}) for i in range(4)] + [Rec(f"d{i}", 2020, {"g": "G"}, {"MESH:OK": "okdrug"}) for i in range(4)],
         "bg": bgc}
    ranked, _ = rank_candidates(a, c, disease_id=D, min_articles=1, min_lift=1.2)
    by = {r["chemical"]: r for _, r in ranked.iterrows()}
    assert by["doca"]["harm_signal"] == "tetikleme sinyali" and by["doca"]["penalty"] == 0.3
    assert by["doca"]["score"] < by["okdrug"]["score"] and by["doca"]["lit_score"] == by["okdrug"]["lit_score"]
    off, _ = rank_candidates(a, c, disease_id=D, min_articles=1, min_lift=1.2, harm_penalty=1.0)
    assert {r["chemical"]: r["penalty"] for _, r in off.iterrows()}["doca"] == 1.0


# ---------- genetik ilişki ve kalıtsal hastalık ----------

def test_two_associations_without_correlation_mean_mutated_not_propagated():
    recs = [Rec(str(i), 2020, {"p": "PDE6A"}, {}, [["Association", "Gene", "p", "Disease", D]]) for i in range(2)]
    recs.append(Rec("x", 2020, {"p": "PDE6A"}, {}, [[POS, "Gene", "p", "Disease", D]]))
    assert gene_disease_direction(recs, ["p"], D) == {"p": "mutated"}


def test_mutated_gene_gives_unknown_consistency_regardless_of_heredity():
    # kayıp-fonksiyon (PDE6B-RP) ile kazanç-fonksiyon (MEFV-FMF) ayırt edilemez; FMF'de IL-1 inhibitörleri 'ters' olmamalı
    assert _consistency("mutated", "inhibit", hereditary=True) == "unknown"
    assert _consistency("mutated", "inhibit", hereditary=False) == "unknown"


def test_mechanism_bonus_halved_for_mutated_gene_and_zero_when_opposite():
    a = [Rec(str(i), 2020, {"p": "PDE6A"}, {}, [["Association", "Gene", "p", "Disease", D]]) for i in range(6)]
    drugs = {"CH1": _drug("dipyridamole", mechanisms=[{"action_type": "INHIBITOR", "moa": "PDE inhibitor", "genes": ["PDE6A"]}])}
    drugs["CH1"]["chembl_id"] = "CH1"
    c = {"p": [Rec("c1", 2020, {"p": "PDE6A"}, {"MESH:DP": "dipyridamole"})]}
    mut, _ = rank_candidates(a, c, drugs=drugs, drug_map={"MESH:DP": "CH1"}, disease_id=D, min_articles=1, hereditary=True)
    assert len(mut) == 1 and mut.iloc[0]["direction"] == "bilinmiyor"
    import math
    assert abs(mut.iloc[0]["mech_score"] - 0.5 * math.log1p(6)) < 1e-3
    # gen yönü ölçülmüş 'azalmış' iken inhibitör: ters -> mekanizma puanı 0
    a2 = [Rec(str(i), 2020, {"p": "PDE6A"}, {}, [[NEG, "Gene", "p", "Disease", D]]) for i in range(6)]
    opp, _ = rank_candidates(a2, c, drugs=drugs, drug_map={"MESH:DP": "CH1"}, disease_id=D, min_articles=1)
    assert opp.iloc[0]["direction"] == "ters" and opp.iloc[0]["mech_score"] == 0.0


# ---------- durum ve sınıf sütunları ----------

def test_status_and_kind_columns():
    bga, bgc = _bg()
    a = [Rec(f"a{i}", 2020, {"g": "G"}, {}) for i in range(5)] + bga
    a += [Rec(f"k{i}", 2020, {}, {"MESH:K": "known"}, [[NEG, "Chemical", "MESH:K", "Disease", D]]) for i in range(2)]
    a += [Rec("l1", 2020, {}, {"MESH:L": "mentioned"})]
    c = {"g": [Rec(f"c{i}", 2020, {"g": "G"}, {"MESH:K": "known", "MESH:L": "mentioned", "MESH:N": "newdrug", "MESH:ANG": "angiotensin"}) for i in range(4)],
         "bg": bgc}
    ranked, _ = rank_candidates(a, c, disease_id=D, min_articles=1, min_lift=1.2)
    by = {r["chemical"]: r for _, r in ranked.iterrows()}
    assert by["known"]["status"] == "tedavi olarak çalışılmış" and by["mentioned"]["status"] == "literatürde anılmış" and by["newdrug"]["status"] == "yeni"
    assert by["angiotensin"]["kind"] == "endojen" and by["newdrug"]["kind"] == "ilaç"


# ---------- klinik çalışma etiketleri (şema 2) ----------

def _study(nct, year, stype="INTERVENTIONAL", purpose="TREATMENT", arms=None, ints=None):
    return {"protocolSection": {
        "identificationModule": {"nctId": nct},
        "statusModule": {"startDateStruct": {"date": f"{year}-03"}},
        "designModule": {"studyType": stype, "designInfo": {"primaryPurpose": purpose}, "phases": ["PHASE2"]},
        "armsInterventionsModule": {"armGroups": arms or [], "interventions": ints or []},
    }}


def test_trials_only_count_experimental_arm_of_interventional_treatment_studies():
    payload = {"studies": [
        _study("N1", 2020, arms=[{"type": "EXPERIMENTAL", "interventionNames": ["Drug: Newdrug"]},
                                 {"type": "ACTIVE_COMPARATOR", "interventionNames": ["Drug: Oldcomparator"]}],
               ints=[{"type": "DRUG", "name": "Newdrug"}, {"type": "DRUG", "name": "Oldcomparator"}]),
        _study("N2", 2019, stype="OBSERVATIONAL", ints=[{"type": "DRUG", "name": "Observed"}]),
        _study("N3", 2021, purpose="DIAGNOSTIC", ints=[{"type": "DRUG", "name": "Contrastagent"}]),
        _study("N4", 2018, ints=[{"type": "DRUG", "name": "Legacyonly"}]),  # kol bilgisi yok: tüm müdahaleler
    ]}
    trials = parse_trials(payload)
    assert trials[0]["experimental"] == ["Newdrug"] and trials[1]["study_type"] == "OBSERVATIONAL"
    drugs = [_drug(n) for n in ("newdrug", "oldcomparator", "observed", "contrastagent", "legacyonly")]
    first = map_trials_to_drugs(trials, drugs, 1.0)
    assert first == {"CH_NEWDRUG": 2020, "CH_LEGACYONLY": 2018}


# ---------- model organizma genleri ----------

def test_mouse_gene_merged_into_human_ortholog():
    info = {"5158": {"symbol": "PDE6B", "taxid": 9606}, "18587": {"symbol": "Pde6b", "taxid": 10090}}
    a = [Rec("a1", 2020, {"18587": "rd1"}, {}, [["Association", "Gene", "18587", "Disease", D]]), Rec("a2", 2020, {"5158": "PDE6B"}, {})]
    c = {"18587": [Rec("c1", 2020, {"18587": "rd1"}, {"MESH:Z": "z"})], "5158": [Rec("c2", 2020, {"5158": "PDE6B"}, {"MESH:Z": "z"})]}
    mapping = merge_orthologs(a, c, info)
    assert mapping == {"18587": "5158"}
    assert "5158" in a[0].genes and "18587" not in a[0].genes and a[0].relations[0][2] == "5158"
    assert "18587" not in c and {r.pmid for r in c["5158"]} == {"c1", "c2"}


def test_clinicaltrials_field_set_fallback_on_400():
    import requests

    from kesif_engine.sources import clinicaltrials

    class Resp:
        status_code = 400

    class Client:
        used = []

        def request(self, method, url, params=None, **kw):
            Client.used.append(params["fields"])
            if params["fields"] in clinicaltrials.CT_FIELD_SETS[:2]:
                err = requests.HTTPError("400")
                err.response = Resp()
                raise err

            class R:
                @staticmethod
                def json():
                    return {"studies": [_study("N1", 2020, ints=[{"type": "DRUG", "name": "X"}])]}
            return R()

    trials = clinicaltrials.fetch_trials(Client(), "Test Disease")
    assert len(trials) == 1 and Client.used[-1] == clinicaltrials.CT_FIELD_SETS[2]
