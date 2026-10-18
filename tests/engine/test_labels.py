from kesif_engine.evaluation import labels
from kesif_engine.evaluation.backtest import backtest
from kesif_engine.sources import clinicaltrials
from tests.engine.demo_data import DISEASE, demo_drugs, make_corpus

CT_PAYLOAD = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT1"},
                "statusModule": {"startDateStruct": {"date": "2019-03"}},
                "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "Apremilast 30 mg BID"}, {"type": "OTHER", "name": "Placebo"}]},
            }
        },
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT2"},
                "statusModule": {"startDateStruct": {"date": "2012-11-01"}},
                "armsInterventionsModule": {"interventions": [{"type": "BIOLOGICAL", "name": "Infliximab (Remicade)"}]},
            }
        },
        {"protocolSection": {"identificationModule": {"nctId": "NCT3"}, "statusModule": {}}},
    ],
    "nextPageToken": None,
}


def test_parse_trials_and_intervention_candidates():
    trials = clinicaltrials.parse_trials(CT_PAYLOAD)
    assert [t["nct"] for t in trials] == ["NCT1", "NCT2", "NCT3"]
    assert trials[0]["start_year"] == 2019 and trials[2]["start_year"] == 0
    assert labels.intervention_candidates("Apremilast 30 mg BID")[1:] == ["Apremilast"]
    assert "Infliximab" in labels.intervention_candidates("Infliximab (Remicade)")


def test_map_trials_to_drugs_takes_earliest_year_and_skips_non_drugs():
    drugs = [
        {"chembl_id": "C_APR", "name": "APREMILAST", "max_phase": 4.0, "synonyms": [], "mechanisms": []},
        {"chembl_id": "C_INF", "name": "INFLIXIMAB", "max_phase": 4.0, "synonyms": ["Remicade"], "mechanisms": []},
        {"chembl_id": "C_PLA", "name": "PLACEBO", "max_phase": 4.0, "synonyms": [], "mechanisms": []},
    ]
    first = labels.map_trials_to_drugs(clinicaltrials.parse_trials(CT_PAYLOAD), drugs, 1.0)
    assert first == {"C_APR": 2019, "C_INF": 2012}


def test_backtest_schemes_therapy_and_trials():
    a, c = make_corpus()
    drugs, drug_map = demo_drugs()
    # tedavi makaleleri: 2016+ ve gelecek ilaçlarını içeren hastalık makaleleri
    therapy = [r.pmid for r in a if r.year > 2015 and any(k.startswith("F") for k in r.chemicals)]
    lab = {"therapy_pmids": therapy, "first_trial_year": {"CHEMBLF1": 2017, "CHEMBLF2": 2019, "CHEMBLK1": 2005}}
    r = backtest(a, c, 2015, drugs=drugs, drug_map=drug_map, disease_id=DISEASE, labels=lab)
    assert set(r["schemes"]) == {"mentions", "therapy", "trials", "trials_novel"}
    assert "n_universe" in r["schemes"]["trials_novel"]
    assert r["primary_scheme"] == "trials"
    th = r["schemes"]["therapy"]
    assert {p["drug"] for p in th["positives"]} >= {"futuredrug1", "futuredrug2", "futuredrug3"}
    assert th["metrics"]["method"]["hits_at_10"] >= 3
    tr = r["schemes"]["trials"]
    assert {p["drug"] for p in tr["positives"]} == {"futuredrug1", "futuredrug2"}
    assert tr["metrics"]["method"]["hits_at_10"] == 2
    assert "knowndrug1" not in {p["drug"] for p in tr["positives"]}  # kesit öncesi çalışması var


def test_backtest_without_labels_only_mentions():
    a, c = make_corpus()
    r = backtest(a, c, 2015)
    assert list(r["schemes"]) == ["mentions"] and r["primary_scheme"] == "mentions"
    assert "metrics" in r
