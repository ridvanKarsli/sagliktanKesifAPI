import json

import pandas as pd

from kesif_engine import health
from kesif_engine.insights import discovery
from kesif_server.infrastructure.engine.adapter import EngineAdapter

_is_network_error = EngineAdapter().is_network_error


def _write(results, data):
    results.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"rank": 1, "chemical_id": "a", "chemical": "knowndrug", "chembl_id": "CH1", "max_phase": 4.0, "kind": "ilaç", "status": "tedavi olarak çalışılmış", "score": 50, "lit_score": 50, "mech_score": 0, "penalty": 1, "direction": "tutarlı", "harm_signal": "tedavi yönü", "n_treat_rel": 5, "n_harm_rel": 0, "n_bridges": 1, "mechanism": "", "n_direct_a": 10},
        {"rank": 2, "chemical_id": "b", "chemical": "newdrug", "chembl_id": "CH2", "max_phase": 3.0, "kind": "ilaç", "status": "yeni", "score": 30, "lit_score": 30, "mech_score": 0, "penalty": 1, "direction": "tutarlı", "harm_signal": "yok", "n_treat_rel": 0, "n_harm_rel": 0, "n_bridges": 1, "mechanism": "", "n_direct_a": 0},
        {"rank": 3, "chemical_id": "c", "chemical": "hypothesis", "chembl_id": "CH3", "max_phase": 2.0, "kind": "ilaç", "status": "yeni", "score": 20, "lit_score": 0, "mech_score": 20, "penalty": 1, "direction": "bilinmiyor", "harm_signal": "yok", "n_treat_rel": 0, "n_harm_rel": 0, "n_bridges": 0, "mechanism": "inhibitor G2", "n_direct_a": 0},
        {"rank": 4, "chemical_id": "d", "chemical": "angiotensin", "chembl_id": "CH4", "max_phase": 3.0, "kind": "endojen", "status": "yeni", "score": 10, "lit_score": 10, "mech_score": 0, "penalty": 1, "direction": "bilinmiyor", "harm_signal": "yok", "n_treat_rel": 0, "n_harm_rel": 0, "n_bridges": 1, "mechanism": "", "n_direct_a": 0},
    ]).to_csv(results / "candidates.csv", index=False)
    pd.DataFrame([
        {"rank": 2, "chemical_id": "b", "clinical_trials": 2, "pubmed_direct": 3, "durum": "klinik çalışma kaydı var"},
        {"rank": 3, "chemical_id": "c", "clinical_trials": 0, "pubmed_direct": 0, "durum": "doğrudan kayıt yok (aday)"},
    ]).to_csv(results / "candidates_novelty.csv", index=False)
    (results / "evidence.json").write_text(json.dumps({
        "b": [{"source": "literature", "gene": "G1", "n_ab": 40, "n_bc": 6, "lift": 7.5, "gene_direction": "up", "gene_direction_source": "relations", "drug_effect": "inhibit", "consistency": "consistent", "contribution": 30}],
        "c": [{"source": "chembl", "gene": "G2", "n_ab": 12, "action_type": "INHIBITOR", "moa": "G2 inhibitor", "gene_direction": "unknown", "gene_direction_source": "unknown", "drug_effect": "inhibit", "consistency": "unknown", "contribution": 20}],
    }), encoding="utf-8")
    (results / "backtest.json").write_text(json.dumps({"cutoff_year": 2018, "n_universe_total": 100, "dropped_bridges": [["1", "TNF", 0.3]],
        "schemes": {"trials": {"n_positives": 5, "n_universe": 90, "random_expected_hits": {"at_10": 0.5}, "metrics": {"method": {"auc": 0.7, "hits_at_10": 2, "hits_at_25": 3}, "frequency": {"auc": 0.6, "hits_at_10": 1, "hits_at_25": 2}}}}}), encoding="utf-8")
    (data / "labels.json").write_text(json.dumps({"schema": 2, "therapy_pmids": [], "trials": [], "first_trial_year": {"CH1": 2019, "CH2": 2020, "CH9": 2021}}), encoding="utf-8")


def test_discovery_summary_two_tier_novelty_and_justification(tmp_path):
    results, data = tmp_path / "r", tmp_path / "d"
    _write(results, data)
    s = discovery.build_summary(results, data, {"key": "x", "label": "Test hastalığı"})
    assert s["n_new"] == 2 and s["n_new_checked"] == 2 and s["n_new_with_trials"] == 1 and s["n_untested"] == 1
    assert s["new_with_trials"][0]["chemical"] == "newdrug"
    assert s["base_rate"] == 0.03 and s["enrichment"] == round((0.5 / 0.03), 1)
    by = {c["chemical"]: c for c in s["new_candidates"]}
    assert "görmeden buldu" in by["newdrug"]["justification"] and "G1 geni 40 hastalık makalesinde" in by["newdrug"]["justification"]
    assert "tek kaynaklı" in by["hypothesis"]["justification"]
    assert "angiotensin" not in by  # endojen sınıfı keşif listesine girmez
    assert s["target_classes"][0]["gene"] in ("G1", "G2")
    assert "sıklık sıralamasını geçiyor (2 / 1)" in s["verdict"]
    assert (results / "discovery.json").exists()
    rows = discovery.compare(tmp_path, {"r": {"label": "R"}})
    assert rows[0]["n_new_with_trials"] == 1 and rows[0]["trials_at_10"] == "2 / 1"


def test_network_error_detection_for_job_retry():
    import requests

    assert _is_network_error(requests.ConnectionError("x"))
    assert _is_network_error(RuntimeError("İstek başarısız oldu: https://x (HTTP 503)"))
    assert not _is_network_error(KeyError("kind"))


def test_services_checks_report_failures_without_raising():
    class Dead:
        def request(self, *a, **k):
            raise ConnectionError("offline")

    out = health.check_all(Dead())
    assert len(out) == 5 and not any(r["ok"] for r in out) and all("ConnectionError" in r["detail"] for r in out)
