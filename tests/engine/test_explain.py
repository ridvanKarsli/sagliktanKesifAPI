"""Kanıt zinciri: katkı payları ve kaynaklı adımlar."""

from __future__ import annotations

import json

import pandas as pd

from kesif_engine.insights import explain
from kesif_engine.sources import trial_atlas


def test_chain_has_contributions_literature_and_neighbor_steps():
    feats = pd.DataFrame({"similar": [3.0, 0.0, 0.0], "frequency": [5.0, 50.0, 1.0], "accel": [1.0, 0.0, 0.0]},
                         index=["C1", "C2", "C3"])
    decision = {"method": "stack", "model": {"features": ["similar", "frequency", "accel"], "coef": [5.0, 0.3, 1.0]}}
    ranked = pd.DataFrame({"chemical_id": ["M1", "M2"], "chemical": ["alphamab", "betanib"], "genetic_target": ["IL23R 0.81", ""],
                           "network_target": ["", ""]})
    evidence = {"M1": [{"source": "literature", "gene": "IL23R", "n_bc": 12, "n_ab": 40, "lift": 9.1, "drug_effect": "inhibit",
                        "consistency": "consistent", "contribution": 3.0, "bc_pmids": ["111", "112", "113"], "ab_pmids": ["221"]}]}
    atlas = trial_atlas.TrialAtlas({"first_year": {"D1": {"C9": 2000, "C8": 2001, "C7": 2002},
                                                   "D2": {"C9": 2005, "C8": 2006, "C1": 2010, "C7": 2003}},
                                    "first_nct": {"D2": {"C1": "NCT0001"}}, "names": {"D2": "Uveitis"}})
    ext = {"atlas": atlas, "atlas_own": {"C9": 2000, "C8": 2001, "C7": 2002}, "atlas_codes": ["D1"]}
    chains = explain.build(ranked, feats, decision, evidence, ext, {"M1": "C1", "M2": "C2"}, "Behçet")
    c1 = chains["M1"]
    assert c1["katkilar"][0]["kanal"] == "similar" and abs(sum(k["pay"] for k in c1["katkilar"]) - 1) < 0.01
    kinds = [a["tur"] for a in c1["adimlar"]]
    assert kinds[0] == "benzer hastalık" and "literatür köprüsü" in kinds and "genetik" in kinds
    assert {"tip": "nct", "id": "NCT0001"} in c1["adimlar"][0]["kaynaklar"]
    lit = next(a for a in c1["adimlar"] if a["tur"] == "literatür köprüsü")
    assert "IL23R" in lit["metin"] and "baskılıyor" in lit["metin"] and {"tip": "pmid", "id": "111"} in lit["kaynaklar"]
    # popüler ama başka kanıtı olmayan aday: katkı yalnız popülerlikten
    assert [k["kanal"] for k in chains["M2"]["katkilar"]] == ["frequency"]
    col = explain.to_column(chains, ["M1", "X"])
    assert json.loads(col[0])["adimlar"] and col[1] == ""
