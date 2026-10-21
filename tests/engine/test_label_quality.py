"""Faz 1: etiket temizliği, ikinci etiket, elle denetim, sızıntı raporu ve kapı 1."""

from __future__ import annotations

from kesif_engine.evaluation import audit, benchmark, labels
from kesif_engine.evaluation.backtest import backtest
from kesif_engine.sources import chembl, clinicaltrials
from tests.engine.demo_data import DISEASE, demo_drugs, make_corpus

DRUGS = [
    {"chembl_id": "C_NEW", "name": "NOVOMAB", "max_phase": 3.0, "synonyms": [], "mechanisms": [], "parent": "C_NEW"},
    {"chembl_id": "C_MTX", "name": "METHOTREXATE", "max_phase": 4.0, "synonyms": [], "mechanisms": [], "parent": "C_MTX"},
    {"chembl_id": "C_MET", "name": "METFORMIN", "max_phase": 4.0, "synonyms": [], "mechanisms": [], "parent": "C_MET"},
    {"chembl_id": "C_METHCL", "name": "METFORMIN HYDROCHLORIDE", "max_phase": 4.0, "synonyms": [], "mechanisms": [], "parent": "C_MET"},
    {"chembl_id": "C_FDG", "name": "FLUDEOXYGLUCOSE", "max_phase": 4.0, "synonyms": [], "mechanisms": [], "parent": "C_FDG"},
    {"chembl_id": "C_DUL", "name": "DULOXETINE", "max_phase": 4.0, "synonyms": [], "mechanisms": [], "parent": "C_DUL"},
]


def _study(nct, date, interventions, arms, phases=("PHASE2",), conditions=("Asthma",)):
    return {"protocolSection": {
        "identificationModule": {"nctId": nct},
        "statusModule": {"startDateStruct": {"date": date}},
        "designModule": {"studyType": "INTERVENTIONAL", "designInfo": {"primaryPurpose": "TREATMENT"}, "phases": list(phases)},
        "armsInterventionsModule": {
            "interventions": [{"type": t, "name": n} for t, n in interventions],
            "armGroups": [{"type": t, "interventionNames": [f"Drug: {x}" for x in names]} for t, names in arms],
        },
        "conditionsModule": {"conditions": list(conditions)},
    }}


PAYLOAD = {"studies": [
    # yeni ilaç + arka plan metotreksat, plasebo kolu da metotreksat alıyor → yalnızca yeni ilaç sayılır
    _study("NCT10", "2019-01", [("DRUG", "Novomab"), ("DRUG", "Methotrexate"), ("DRUG", "Placebo")],
           [("EXPERIMENTAL", ["Novomab", "Methotrexate"]), ("PLACEBO_COMPARATOR", ["Placebo", "Methotrexate"])]),
    # tuz biçimiyle eski çalışma, ana molekülle yeni çalışma → ana molekülün yılı 2005
    _study("NCT11", "2005-06", [("DRUG", "Metformin Hydrochloride")], [("EXPERIMENTAL", ["Metformin Hydrochloride"])], ("PHASE1",)),
    _study("NCT12", "2021-03", [("DRUG", "Metformin")], [("EXPERIMENTAL", ["Metformin"])]),
    # görüntüleme ajanı
    _study("NCT13", "2020-02", [("DRUG", "Fludeoxyglucose")], [("EXPERIMENTAL", ["Fludeoxyglucose"])]),
    # şemsiye çalışma: 6 hastalık
    _study("NCT14", "2021-05", [("DRUG", "Duloxetine")], [("EXPERIMENTAL", ["Duloxetine"])],
           conditions=("Pain", "Asthma", "COPD", "Depression", "Anxiety", "Fibromyalgia")),
]}


def test_parse_keeps_arm_composition_and_conditions():
    t = clinicaltrials.parse_trials(PAYLOAD)[0]
    assert t["arms"][1] == {"type": "PLACEBO_COMPARATOR", "names": ["Placebo", "Methotrexate"]}
    assert t["conditions"] == ["Asthma"] and t["phases"] == ["PHASE2"]


def test_label_cleaning_rules():
    trials = clinicaltrials.parse_trials(PAYLOAD)
    first, late, sources = labels.map_trials_to_drugs(trials, DRUGS, 1.0, with_sources=True)
    assert first["C_NEW"] == 2019 and "C_MTX" not in first          # arka plan tedavisi sayılmaz
    assert first["C_MET"] == 2005 and first["C_METHCL"] == 2005      # tuz biçimi ana moleküle birleşir
    assert "C_FDG" not in first                                      # görüntüleme ajanı
    assert "C_DUL" not in first                                      # şemsiye çalışma
    assert late["C_MET"] == 2021                                     # faz 2+ etiketinde ilk yıl ayrı
    assert sources["C_NEW"] == {"nct": "NCT10", "year": 2019, "name": "Novomab", "phase": 2}


def test_canonical_ids_maps_salts_only_when_parent_is_known():
    canon = chembl.canonical_ids(DRUGS + [{"chembl_id": "C_ORPHAN", "name": "X SODIUM", "max_phase": 4.0, "parent": "C_MISSING"}])
    assert canon["C_METHCL"] == "C_MET" and canon["C_ORPHAN"] == "C_ORPHAN"


def test_audit_round_trip_and_merge():
    trials = clinicaltrials.parse_trials(PAYLOAD)
    _, _, sources = labels.map_trials_to_drugs(trials, DRUGS, 1.0, with_sources=True)
    lab = {"first_trial_source": sources}
    rows = audit.sample(lab, DRUGS, n=10, min_year=2010)
    assert [r["chembl_id"] for r in rows] == ["C_NEW"]  # metforminin ilk yılı 2005: denetim bölgesinin dışında
    assert audit.sample(lab, DRUGS, n=10, min_year=2000)[0]["chembl_id"] == "C_MET"
    assert rows[0]["baglanti"] == "https://clinicaltrials.gov/study/NCT10"
    text = audit.to_csv(rows).replace("https://clinicaltrials.gov/study/NCT10;;", "https://clinicaltrials.gov/study/NCT10;hayır;arka plan")
    one = audit.read_marked(text)
    assert one["n_checked"] == 1 and one["n_wrong"] == 1 and one["error_rate"] == 1.0
    ok_round = audit.read_marked(audit.to_csv(rows).replace("NCT10;;", "NCT10;evet;"))
    both = audit.merge(one, ok_round)
    assert both["n_checked"] == 2 and both["n_wrong"] == 1 and both["rounds"] == 2
    assert audit.excluded_ids(both) == {"C_NEW"}
    try:
        audit.read_marked(audit.to_csv(rows).replace("NCT10;;", "NCT10;belki;"))
        raise AssertionError("tanınmayan işaret kabul edildi")
    except ValueError:
        pass


def test_backtest_reports_leakage_second_label_ltc_and_respects_audit():
    a, c = make_corpus()
    drugs, drug_map = demo_drugs()
    lab = {"first_trial_year": {"CHEMBLF1": 2017, "CHEMBLF2": 2019, "CHEMBLK1": 2005},
           "first_late_trial_year": {"CHEMBLF1": 2018, "CHEMBLK1": 2006}}
    r = backtest(a, c, 2015, drugs=drugs, drug_map=drug_map, disease_id=DISEASE, labels=lab)
    assert {"ltc", "frequency", "method"} <= set(r["schemes"]["trials"]["metrics"])
    assert r["schemes"]["trials_late"]["n_positives"] == 1
    inputs = {x["input"] for x in r["leakage"]["not_time_sliced"]}
    assert "ilaç sözlüğü" in inputs and "doğrulama etiketleri" in r["leakage"]["time_sliced"]
    r2 = backtest(a, c, 2015, drugs=drugs, drug_map=drug_map, disease_id=DISEASE, labels={**lab, "audit_excluded": ["CHEMBLF2"]})
    assert {p["drug"] for p in r2["schemes"]["trials"]["positives"]} == {"futuredrug1"}


def test_ranking_ignores_records_after_cutoff():
    """Sızıntı testi: kesitten sonraki makaleler ne kadar değişirse değişsin kesit sıralaması aynı kalır."""
    a, c = make_corpus()
    drugs, drug_map = demo_drugs()
    base = backtest(a, c, 2015, drugs=drugs, drug_map=drug_map, disease_id=DISEASE)
    future_noise = [r for r in a if r.year and r.year > 2015]
    for r in future_noise:
        r.chemicals = {"P1": "popdrug1"}
    changed = backtest(a, c, 2015, drugs=drugs, drug_map=drug_map, disease_id=DISEASE)
    assert base["n_scored_by_method"] == changed["n_scored_by_method"]
    m1 = base["schemes"]["mentions"]
    assert m1["n_universe"] == changed["schemes"]["mentions"]["n_universe"]


def test_gate_1_needs_diseases_audit_and_baselines():
    rows = [{"base_ap": 0.3, "rrf_core_ap": 0.4, "frequency_ap": 0.1, "ltc_ap": 0.2} for _ in range(6)]
    decision = {"measured_as": "rrf_core"}
    inputs = {d: {"audit": {"n_checked": 50, "n_wrong": 1}} for d in ("a", "b")}
    g = benchmark.gate_1(rows, decision, inputs, ["a", "b"], n_boot=200)
    assert g["checks"]["label_error"]["ok"] and g["checks"]["beats_baselines"]["ok"]
    assert not g["passed"] and g["missing"] == ["diseases"]
    g2 = benchmark.gate_1(rows, decision, {"a": {}, "b": {}}, ["a", "b"], n_boot=200)
    assert not g2["checks"]["label_error"]["ok"]


def test_schema4_trial_must_belong_to_target_disease():
    payload = {"studies": [{
        "protocolSection": {"identificationModule": {"nctId": "NCT20", "briefTitle": "Anakinra in Behcet's Disease"},
                            "conditionsModule": {"conditions": ["Behcet Syndrome"]}},
        "derivedSection": {"conditionBrowseModule": {"meshes": [{"id": "D001528", "term": "Behcet Syndrome"}],
                                                     "ancestors": [{"id": "D014657", "term": "Vasculitis"}]}}}]}
    t = clinicaltrials.parse_trials(payload)[0]
    assert t["condition_mesh"] == ["D001528", "D014657"] and t["title"].startswith("Anakinra")
    assert labels.trial_is_about(t, "MESH:D001528", labels.disease_terms("Behcet Syndrome"))
    assert not labels.trial_is_about(t, "MESH:D010505", labels.disease_terms("Familial Mediterranean Fever"))
    # alt tip: üst kodlar da sayılır (lupus nefriti → SLE)
    assert labels.trial_is_about({"condition_mesh": ["D008181", "D008180"]}, "MESH:D008180", [])
    # kod yoksa ad eşlemesi, ters çevrilmiş MeSH adı doğal sıraya çevrilir
    t2 = {"condition_mesh": None, "title": "", "conditions": ["Rheumatoid Arthritis"]}
    assert labels.trial_is_about(t2, "MESH:D001172", labels.disease_terms("Arthritis, Rheumatoid"))


def test_schema4_excludes_healthy_pk_procedural_and_support_trials():
    base = {"phases": ["PHASE1"], "interventions": []}
    assert labels.exclusion_reason({**base, "title": "A Study of X", "healthy_volunteers": True}) == "healthy"
    assert labels.exclusion_reason({**base, "phases": ["PHASE3"], "title": "X in Healthy Adult Volunteers"}) == "healthy"
    assert labels.exclusion_reason({**base, "phases": ["PHASE3"], "title": "A Study of X", "healthy_volunteers": True}) == ""
    assert labels.exclusion_reason({"title": "Drug-Drug Interaction of X With Midazolam"}) == "pk"
    assert labels.exclusion_reason({"title": "Ketamine Sedation for Muscle Biopsy"}) == "procedural"
    assert labels.exclusion_reason({"title": "Efficacy in Biopsy-Confirmed NASH"}) == ""
    assert labels.exclusion_reason({"title": "Tolerance", "interventions": [("PROCEDURE", "Stem cell transplantation"), ("DRUG", "Melphalan")]}) == "support"
    assert labels.exclusion_reason({"title": "Phase 3 Study of Apremilast", "interventions": [("DRUG", "Apremilast")]}) == ""


def test_audit_rounds_reset_rate_on_new_label_schema_but_keep_exclusions():
    old = audit.read_marked("chembl_id;dogru_mu\nC1;hayır\nC2;evet\n", labels_schema=3)
    new = audit.read_marked("chembl_id;dogru_mu\nC3;evet\nC4;evet\n", labels_schema=4)
    m = audit.merge(old, new)
    assert m["n_checked"] == 2 and m["n_wrong"] == 0 and m["history"][0]["error_rate"] == 0.5
    assert audit.excluded_ids(m) == {"C1"} and m["checked_ids"] == ["C1", "C2", "C3", "C4"]
    same = audit.merge(m, audit.read_marked("chembl_id;dogru_mu\nC5;hayır\n", labels_schema=4))
    assert same["n_checked"] == 3 and same["n_wrong"] == 1
    lab = {"first_trial_source": {f"C{i}": {"nct": "N", "year": 2015} for i in range(1, 8)}}
    rows = audit.sample(lab, [], n=10, skip=set(same["checked_ids"]))
    assert {r["chembl_id"] for r in rows} == {"C6", "C7"}
    gate = benchmark.gate_1([{"base_ap": 0.3, "rrf_core_ap": 0.4, "frequency_ap": 0.1, "ltc_ap": 0.1}] * 4,
                            {"measured_as": "rrf_core"}, {"a": {"audit": old, "labels": {"schema": 4}}}, ["a"], n_boot=100)
    assert gate["checks"]["label_error"]["n_checked"] == 0  # eski şemanın denetimi kapıya sayılmaz


def test_schema5_disease_must_be_named_and_not_a_different_disease():
    ht = labels.vocabulary("Hypertension", "MESH:D006973")
    ph = {"condition_mesh": ["D006973", "D006976"], "conditions": ["Pulmonary Arterial Hypertension"], "title": "X in PAH"}
    eh = {"condition_mesh": ["D000075222", "D006973"], "conditions": ["Essential Hypertension"], "title": "X"}
    assert not labels.trial_is_about(ph, "MESH:D006973", *ht) and labels.trial_is_about(eh, "MESH:D006973", *ht)
    ra = labels.vocabulary("Arthritis, Rheumatoid", "MESH:D001172")
    sj = {"condition_mesh": ["D012859", "D001172"], "conditions": ["Sjogren's Syndrome"], "title": "Y in Sjögren's"}
    assert not labels.trial_is_about(sj, "MESH:D001172", *ra)
    t2 = labels.vocabulary("Diabetes Mellitus, Type 2", "MESH:D003924")
    assert labels.trial_is_about({"condition_mesh": ["D003924"], "conditions": ["T2DM"], "title": ""}, "MESH:D003924", *t2)
    bh = labels.vocabulary("Behcet Syndrome", "MESH:D001528")
    assert labels.trial_is_about({"condition_mesh": None, "conditions": ["Behçet's Disease"], "title": ""}, "MESH:D001528", *bh)
    # bilinmeyen hastalık: sözlük yoksa adın kendisi
    assert labels.vocabulary("Arthritis, Reactive", "MESH:D016918")[0] == ["reactive arthritis", "arthritis, reactive"]


def test_schema5_excludes_comorbidity_vaccine_response_and_discontinuation():
    assert labels.exclusion_reason({"title": "Iron Deficiency Anaemia in Heart Failure"}) == "comorbidity"
    assert labels.exclusion_reason({"title": "Response to Zoster Vaccine in RA Patients on Tofacitinib"}) == "comorbidity"
    assert labels.exclusion_reason({"title": "Methotrexate Discontinuation in Stable Crohn's"}) == "discontinuation"
    assert labels.exclusion_reason({"title": "Active Immunotherapy With an Amyloid Vaccine in Alzheimer's Disease"}) == ""


def test_gate_label_error_is_residual_after_audit_coverage():
    src = {f"C{i}": {"nct": "N", "year": 2015} for i in range(10)}
    rows = [{"base_ap": 0.3, "rrf_core_ap": 0.4, "frequency_ap": 0.1, "ltc_ap": 0.1}] * 4
    half = {"labels_schema": 5, "n_checked": 5, "n_wrong": 1}
    full = {"labels_schema": 5, "n_checked": 10, "n_wrong": 2}
    lab = {"schema": 5, "first_trial_source": src}
    g = benchmark.gate_1(rows, {"measured_as": "rrf_core"}, {"a": {"audit": half, "labels": lab}}, ["a"], n_boot=100)
    le = g["checks"]["label_error"]
    assert le["raw_error"] == 0.2 and le["coverage"] == 0.5 and le["value"] == 0.1 and not le["ok"]
    g = benchmark.gate_1(rows, {"measured_as": "rrf_core"}, {"a": {"audit": full, "labels": lab}}, ["a"], n_boot=100)
    assert g["checks"]["label_error"]["value"] == 0.0 and g["checks"]["label_error"]["ok"]


def test_final_confirmation_requires_lower_bound_above_frequency():
    import pandas as pd
    f = pd.DataFrame({"base": [3.0, 2.0, 1.0, 0.5], "lit": [0.0, 0.0, 1.0, 2.0], "accel": [0.0, 0.0, 2.0, 1.0],
                      "penalty": [1.0] * 4}, index=["a", "b", "c", "d"])
    good = [{"disease": "x", "cutoff": c, "features": f, "positives": {"c"}, "frequency_ap": 0.25, "random_ap": 0.25}
            for c in (2012, 2015, 2018)]
    dec = {"method": "rrf", "channels": ["accel", "lit"], "measured_as": "rrf_ivme"}
    r = benchmark.final_confirmation(dec, good, n_boot=100)
    assert r["ok"] and r["vs_frequency"]["lo"] > 0 and r["groups"][0]["chosen_ap"] == 1.0
    bad = [{**g, "frequency_ap": 1.0} for g in good]
    assert not benchmark.final_confirmation(dec, bad, n_boot=100)["ok"]
    assert not benchmark.final_confirmation(dec, [], n_boot=100)["ok"]


def test_gate_does_not_pass_while_final_set_is_missing():
    rows = [{"base_ap": 0.3, "rrf_core_ap": 0.4, "frequency_ap": 0.1, "ltc_ap": 0.1}] * 4
    inputs = {"a": {"audit": {"n_checked": 50, "n_wrong": 0}}}
    missing = benchmark.final_confirmation({"method": "base"}, [], n_boot=50)
    g = benchmark.gate_1(rows * 7, {"measured_as": "rrf_core"}, inputs, ["a"] * 26, n_boot=50, final_check=missing)
    assert not g["passed"] and "final_confirmation" in g["missing"]
