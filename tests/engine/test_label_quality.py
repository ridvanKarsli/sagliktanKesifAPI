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
