"""Tahmin defteri: seçim kuralları, değiştirilemezlik, özet doğrulama ve sonradan değerlendirme."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from kesif_engine.catalog import diseases
from kesif_engine.core import config
from kesif_engine.pipeline import ledger
from kesif_engine.sources import trial_atlas


def _cands():
    rows = [
        # (rank, chembl, ad, tür, durum)
        (1, "C1", "studied", "ilaç", "tedavi olarak çalışılmış"),
        (2, "C2", "already trialed", "ilaç", "literatürde anılmış"),
        (3, "C3", "endogenous", "endojen", "yeni"),
        (4, "C4", "novel one", "ilaç", "yeni"),
        (5, "C5", "mentioned", "ilaç", "literatürde anılmış"),
        (6, "C6", "supplement", "besin/takviye", "yeni"),
        (7, "C7", "atlas trialed", "ilaç", "yeni"),
        (8, "C4", "duplicate id", "ilaç", "yeni"),
    ]
    return pd.DataFrame([{"rank": r, "chembl_id": c, "chemical": n, "kind": k, "status": s, "score": 1.0 / r,
                          "accel_score": 0.5, "similar_score": 0.2, "fusion": "stack"} for r, c, n, k, s in rows])


def test_select_rules():
    entries, universe = ledger.select(_cands(), tried={"C2", "C7"}, top=2)
    assert [e["chembl_id"] for e in entries] == ["C4", "C5"]
    assert universe == ["C4", "C5", "C6"]
    assert entries[0]["channels"]["similar"] == 0.2 and entries[0]["position"] == 1


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path / "results")
    diseases.set_provider(lambda: {"dz": {"key": "dz", "label": "Hastalık", "name_en": "Dz", "mesh_id": "MESH:D1",
                                          "pubmed_query": "dz"}})
    (tmp_path / "results" / "dz").mkdir(parents=True)
    _cands().to_csv(tmp_path / "results" / "dz" / "candidates.csv", index=False)
    (tmp_path / "data" / "dz").mkdir(parents=True)
    (tmp_path / "data" / "dz" / "labels.json").write_text(json.dumps({"built_at": "x", "first_trial_year": {"C2": 2010}}))
    (tmp_path / "data" / "trials").mkdir(parents=True)
    import gzip

    with gzip.open(tmp_path / "data" / "trials" / "atlas.json.gz", "wt", encoding="utf-8") as fh:
        json.dump({"schema": 1, "n_trials": 1, "names": {"D1": "Dz"}, "first_year": {"D1": {"C7": 2015}}}, fh)
    yield tmp_path
    diseases.set_provider(None)


def test_freeze_verify_immutable_and_evaluate(workspace):
    m = ledger.freeze(["dz"], top=2, ledger_id="t1")
    assert m["n_entries"] == 2 and len(m["sha256"]) == 64
    assert ledger.verify("t1")["ok"]
    body = json.loads((ledger.ledger_root() / "t1" / "entries.json").read_text(encoding="utf-8"))
    assert [e["chembl_id"] for e in body["diseases"]["dz"]["entries"]] == ["C4", "C5"]   # C7 atlasta denenmiş
    with pytest.raises(FileExistsError):
        ledger.freeze(["dz"], ledger_id="t1")
    # içerik değişirse doğrulama yakalar
    p = ledger.ledger_root() / "t1" / "entries.json"
    original = p.read_bytes()
    p.write_bytes(original.replace(b"novel one", b"novel two"))
    assert not ledger.verify("t1")["ok"]
    p.write_bytes(original)
    # sonradan: C4 dondurma yılından sonra denendi, C6 (evren, defterde değil) de
    year = int(body["frozen_at_utc"][:4])
    later = trial_atlas.TrialAtlas({"first_year": {"D1": {"C7": 2015, "C4": year + 1, "C6": year + 1, "C5": year}}})
    r = ledger.evaluate("t1", atlas=later)
    assert r["hits"] == 1 and r["n"] == 2
    assert r["expected_random"] == pytest.approx(2 * 2 / 3, abs=1e-3)   # evrenin 3'te 2'si sonradan denendi


def test_canonical_bytes_stable():
    a = ledger.canonical_bytes({"b": 1, "a": "ç"})
    b = ledger.canonical_bytes({"a": "ç", "b": 1})
    assert a == b == '{"a":"ç","b":1}'.encode()


def test_track_counts_only_trials_registered_after_freeze(workspace):
    m = ledger.freeze(["dz"], top=2, ledger_id="t2")
    frozen = m["frozen_at_utc"][:10]

    def trial(nct, drug, submitted, year):
        return {"nct": nct, "title": "Efficacy in Dz", "start_year": year, "study_type": "INTERVENTIONAL", "purpose": "TREATMENT",
                "phases": ["PHASE2"], "interventions": [("DRUG", drug)], "experimental": [drug],
                "arms": [{"type": "EXPERIMENTAL", "names": [drug]}], "conditions": ["Dz"], "condition_mesh": ["D1"],
                "healthy_volunteers": False, "first_submitted": submitted}

    drugs = [{"chembl_id": c, "name": n.upper(), "max_phase": 4.0, "synonyms": [], "mechanisms": [], "parent": c}
             for c, n in (("C4", "novel one"), ("C5", "mentioned"), ("C6", "supplement"))]
    trials = [trial("N1", "novel one", "2099-01-01", 2099),      # dondurmadan sonra: isabet
              trial("N2", "mentioned", "2000-01-01", 2000),      # dondurmadan önce: zaten denenmiş, ölçümden çıkar
              trial("N3", "supplement", "2099-02-01", 2099)]     # evrende ama defterde değil
    r = ledger.track("t2", trials, drugs, 1.0)
    d = r["diseases"][0]
    assert [h["chembl_id"] for h in d["hits"]] == ["C4"] and d["already_trialed_before_freeze"] == ["C5"]
    assert r["n"] == 1 and r["excluded_already_trialed"] == 1 and frozen < "2099"
    assert 0 < r["p_value"] <= 1


def test_blind_panel_mixes_ledger_and_controls(workspace, monkeypatch):
    ledger.freeze(["dz"], top=1, ledger_id="t3")
    monkeypatch.setattr(ledger, "_drug_names", lambda: {"C5": "mentioned", "C6": "supplement"})
    form, key = ledger.blind_panel("t3", per_disease=2)
    assert len(form) == 2 and {k["kaynak"] for k in key} == {"defter", "kontrol"}
    assert all("kaynak" not in f for f in form) and [f["kod"] for f in form] == [k["kod"] for k in key]
