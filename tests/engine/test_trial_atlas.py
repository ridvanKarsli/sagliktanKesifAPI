"""Klinik çalışma atlası ve komşu hastalık puanı."""

from __future__ import annotations

import gzip
import json

from kesif_engine.evaluation import atlas as atlas_build
from kesif_engine.sources import clinicaltrials, trial_atlas

DRUGS = [
    {"chembl_id": "C_A", "name": "ALPHAMAB", "max_phase": 4.0, "synonyms": [], "mechanisms": [], "parent": "C_A"},
    {"chembl_id": "C_B", "name": "BETANIB", "max_phase": 4.0, "synonyms": [], "mechanisms": [], "parent": "C_B"},
    {"chembl_id": "C_C", "name": "GAMMASTAT", "max_phase": 4.0, "synonyms": [], "mechanisms": [], "parent": "C_C"},
    {"chembl_id": "C_D", "name": "DELTAZOLE", "max_phase": 4.0, "synonyms": [], "mechanisms": [], "parent": "C_D"},
    {"chembl_id": "C_DHCL", "name": "DELTAZOLE HYDROCHLORIDE", "max_phase": 4.0, "synonyms": [], "mechanisms": [], "parent": "C_D"},
    {"chembl_id": "C_E", "name": "EPSILONE", "max_phase": 4.0, "synonyms": [], "mechanisms": [], "parent": "C_E"},
]


def _t(nct, year, drugs, mesh, title="Efficacy study", comparator=None):
    arms = [{"type": "EXPERIMENTAL", "names": list(drugs)}]
    if comparator:
        arms.append({"type": "PLACEBO_COMPARATOR", "names": list(comparator)})
    return {"nct": nct, "title": title, "start_year": year, "study_type": "INTERVENTIONAL", "purpose": "TREATMENT",
            "phases": ["PHASE2"], "interventions": [("DRUG", d) for d in list(drugs) + list(comparator or [])],
            "experimental": list(drugs), "arms": arms, "conditions": [m[1] for m in mesh],
            "condition_mesh_direct": [list(m) for m in mesh], "healthy_volunteers": False}


TRIALS = [
    _t("N1", 2005, ["Alphamab"], [("D1", "Disease one")]),
    _t("N2", 2006, ["Betanib"], [("D1", "Disease one")]),
    _t("N3", 2007, ["Gammastat"], [("D1", "Disease one")]),
    # D2, D1'e benzer (ortak alphamab, betanib) ve kesitten önce deltazole da denenmiş
    _t("N4", 2004, ["Alphamab", "Betanib"], [("D2", "Disease two")]),
    _t("N5", 2008, ["Deltazole Hydrochloride"], [("D2", "Disease two")]),
    # D2'de epsilone kesitten SONRA denenmiş: 2010 kesitinde puan almamalı
    _t("N6", 2015, ["Epsilone"], [("D2", "Disease two")]),
    # D3 alakasız
    _t("N7", 2003, ["Epsilone"], [("D3", "Disease three")]),
    _t("N8", 2003, ["Gammastat"], [("D3", "Disease three")]),
    _t("N9", 2003, ["Deltazole"], [("D3", "Disease three")]),
    # elenecekler: sağlıklı gönüllü farmakokinetik, plasebo kolunda da bulunan ilaç
    _t("N10", 2004, ["Epsilone"], [("D2", "Disease two")], title="Pharmacokinetics of epsilone in healthy volunteers"),
    _t("N11", 2004, ["Epsilone", "Gammastat"], [("D2", "Disease two")], comparator=["Placebo", "Epsilone"]),
]


def test_build_first_years_and_exclusions(tmp_path):
    out = tmp_path / "atlas.json.gz"
    atlas = atlas_build.build(TRIALS, DRUGS, 1.0, out=out)
    fy = atlas["first_year"]
    assert fy["D1"] == {"C_A": 2005, "C_B": 2006, "C_C": 2007}
    assert fy["D2"]["C_D"] == 2008          # tuz biçimi ana moleküle
    assert fy["D2"]["C_E"] == 2015          # sağlıklı gönüllü PK ve plasebo kolundaki epsilone sayılmadı
    assert fy["D2"]["C_C"] == 2004
    assert atlas["stats"].get("pk", 0) + atlas["stats"].get("healthy", 0) == 1
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        assert json.load(fh)["n_trials"] == atlas["n_trials"]


def test_neighbor_scores_time_sliced(tmp_path):
    atlas = trial_atlas.TrialAtlas(atlas_build.build(TRIALS, DRUGS, 1.0, out=tmp_path / "a.json.gz"))
    own = {"C_A": 2005, "C_B": 2006, "C_C": 2007}
    canonical = {"C_DHCL": "C_D"}
    s, used = atlas.neighbor_scores(own, ["MESH:D1"], cutoff=2010, canonical=canonical)
    assert used and used[0]["mesh"] == "D2"
    assert s.get("C_D", 0) > 0 and s["C_DHCL"] == s["C_D"]
    assert "C_E" not in s or s["C_E"] < s["C_D"]   # D2'deki 2015 epsilone kesitte görünmez; yalnız D3'ten gelir
    s2, _ = atlas.neighbor_scores(own, ["MESH:D1"], cutoff=2016)
    assert s2["C_E"] > s.get("C_E", 0)
    # hedef hastalığın kendi kodu komşu sayılmaz; kendi geçmişi kesitten sonraysa puan yok
    assert all(u["mesh"] != "D1" for u in used)
    assert atlas.neighbor_scores({"C_A": 2012}, ["D1"], cutoff=2010) == ({}, [])


def test_direct_mesh_parsed_separately():
    payload = {"studies": [{"protocolSection": {"identificationModule": {"nctId": "NCT1"}},
                            "derivedSection": {"conditionBrowseModule": {
                                "meshes": [{"id": "D001172", "term": "Arthritis, Rheumatoid"}],
                                "ancestors": [{"id": "D001168", "term": "Arthritis"}]}}}]}
    assert trial_atlas._direct_mesh(payload) == {"NCT1": [("D001172", "Arthritis, Rheumatoid")]}
    t = clinicaltrials.parse_trials(payload)[0]
    assert set(t["condition_mesh"]) == {"D001172", "D001168"}


def test_features_similar_channel_respects_reference_year(tmp_path):
    import pandas as pd

    from kesif_engine.scoring import features, fusion

    atlas = trial_atlas.TrialAtlas(atlas_build.build(TRIALS, DRUGS, 1.0, out=tmp_path / "a.json.gz"))
    drugs = {d["chembl_id"]: d for d in DRUGS}
    ext = {"atlas": atlas, "atlas_own": {"C_A": 2005, "C_B": 2006, "C_C": 2007}, "atlas_codes": ["D1"],
           "atlas_canonical": {"C_DHCL": "C_D"}}
    f10 = features.build(pd.DataFrame(), [], [], drugs, {}, ext, universe=["C_D", "C_E"], reference_year=2010)
    f16 = features.build(pd.DataFrame(), [], [], drugs, {}, ext, universe=["C_D", "C_E"], reference_year=2016)
    assert f10.loc["C_D", "similar"] > 0
    assert f16.loc["C_E", "similar"] > f10.loc["C_E", "similar"]
    assert "similar" in features.available_channels(f10)
    assert "similar" in fusion.STACK_SIM_FEATURES and "similar" not in fusion.STACK_FEATURES
    ranked = pd.DataFrame({"chemical_id": ["C_D", "C_A"]})
    notes = features.annotate_external(ranked, drugs, {}, ext)["similar_from"].tolist()
    assert "Disease two" in notes[0]


def test_stack_combine_model_roundtrip():
    import numpy as np
    import pandas as pd

    from kesif_engine.scoring import fusion

    f = pd.DataFrame({n: np.linspace(0, 1, 6) for n in fusion.STACK_SIM_FEATURES}, index=list("abcdef"))
    f["similar"] = [5, 0, 0, 0, 0, 4]
    lab = pd.Series({"a": 1.0, "f": 1.0})
    m = fusion.StackFusion(fusion.STACK_SIM_FEATURES, combine="model").fit([f], [lab])
    d = m.to_dict()
    assert d["combine"] == "model"
    m2 = fusion.StackFusion.from_dict(d)
    assert m2.combine == "model"
    assert np.allclose(m2.predict(f).to_numpy(), m.predict_model(f).to_numpy())
    rrf_model = fusion.StackFusion.from_dict({**d, "combine": "rrf(model, frequency)"})
    assert rrf_model.combine == "rrf"


def test_stop_kind_classifies_reasons():
    def k(status, why):
        return atlas_build.stop_kind({"status": status, "why_stopped": why})
    assert k("TERMINATED", "Futility analysis at interim") == "efficacy"
    assert k("TERMINATED", "Lack of efficacy") == "efficacy"
    assert k("TERMINATED", "The study did not meet its primary endpoint") == "efficacy"
    assert k("SUSPENDED", "Safety concerns; futility") == "efficacy"       # etkisizlik önce
    assert k("TERMINATED", "Unexpected adverse events") == "safety"
    assert k("TERMINATED", "Could not reach the enrollment target") is None  # hasta bulunamaması başarısızlık değil
    assert k("TERMINATED", "Sponsor business decision (pharmaceutical portfolio)") is None
    assert k("WITHDRAWN", "Lack of efficacy in another trial") is None      # hiç başlamamış
    assert k("COMPLETED", "") is None


def test_failed_trials_time_sliced(tmp_path):
    t1 = {**_t("F1", 2006, ["Gammastat"], [("D2", "Disease two")]), "status": "TERMINATED",
          "why_stopped": "Stopped for futility", "completion_date": "2009-03"}
    t2 = {**_t("F2", 2006, ["Epsilone"], [("D3", "Disease three")]), "status": "TERMINATED",
          "why_stopped": "Low enrollment", "completion_date": "2007"}
    atlas = atlas_build.build(TRIALS + [t1, t2], DRUGS, 1.0, out=tmp_path / "a.json.gz")
    assert atlas["failed"]["efficacy"] == {"D2": {"C_C": 2009}}            # yıl = sonlandırma yılı
    assert atlas["failed"]["safety"] == {}
    at = trial_atlas.TrialAtlas(atlas)
    assert at.failed_before(2008) == {}
    assert at.failed_before(2009) == {"C_C": {"D2"}}


def test_stopped_trials_warning_column(tmp_path):
    import pandas as pd

    from kesif_engine.insights import explain
    from kesif_engine.scoring import features

    t1 = {**_t("F1", 2006, ["Gammastat"], [("D2", "Disease two")]), "status": "TERMINATED",
          "why_stopped": "Lack of efficacy", "completion_date": "2009"}
    t2 = {**_t("F2", 2007, ["Gammastat"], [("D3", "Disease three")]), "status": "SUSPENDED",
          "why_stopped": "Hepatotoxicity", "completion_date": "2010"}
    at = trial_atlas.TrialAtlas(atlas_build.build(TRIALS + [t1, t2], DRUGS, 1.0, out=tmp_path / "a.json.gz"))
    assert [r[:3] for r in at.stopped("C_C")] == [("safety", "D3", 2010), ("efficacy", "D2", 2009)]
    ext = {"atlas": at, "atlas_own": {"C_A": 2005, "C_B": 2006}, "atlas_codes": ["MESH:D1"], "atlas_canonical": {}}
    ranked = pd.DataFrame({"chemical_id": ["C_C", "C_A"]})
    col = features._stopped_trials(ranked, {}, ext)
    items = json.loads(col[0])
    assert col[1] == ""
    assert items[0]["nct"] == "F1" and items[0]["benzerlik"]
    assert [i["mesh"] for i in items] == ["D2"]          # D3 bu hastalığa benzemiyor: gösterilmez
    step = explain.stopped_step({"stopped_trials": col[0]})
    assert step["tur"] == "uyarı" and {k["id"] for k in step["kaynaklar"]} == {"F1"}
    assert explain.stopped_step({"stopped_trials": ""}) is None
