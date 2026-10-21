"""Literatürden bağımsız kanallar: genetik (Open Targets), protein ağı (STRING + RWR) ve sızıntı kuralları."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kesif_engine.scoring import features, fusion, network
from kesif_engine.sources import opentargets, string_ppi


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeGraphQL:
    """Open Targets GraphQL yanıtlarını taklit eder."""

    def __init__(self):
        self.calls = []

    def request(self, method, url, json=None, **kwargs):
        self.calls.append(json["variables"])
        q = json["query"]
        if "search(" in q:
            return FakeResponse({"data": {"search": {"hits": [{"id": "EFO_WRONG", "name": "Başka"}, {"id": "EFO_0001", "name": "Behcet"}]}}})
        if "dbXRefs" in q:
            xrefs = ["MESH:D001528", "UMLS:C0004943"] if json["variables"]["id"] == "EFO_0001" else ["MESH:D999999"]
            return FakeResponse({"data": {"disease": {"id": json["variables"]["id"], "name": "Behcet", "dbXRefs": xrefs}}})
        rows = [
            {"target": {"id": "E1", "approvedSymbol": "IL23R"}, "score": 0.8,
             "datatypeScores": [{"id": "genetic_association", "score": 0.9}, {"id": "literature", "score": 0.99}]},
            {"target": {"id": "E2", "approvedSymbol": "TNF"}, "score": 0.7,
             "datatypeScores": [{"id": "known_drug", "score": 1.0}, {"id": "literature", "score": 0.95}]},
            {"target": {"id": "E3", "approvedSymbol": "Erap1"}, "score": 0.5,
             "datatypeScores": [{"id": "animal_model", "score": 0.4}, {"id": "somatic_mutation", "score": 0.0}]},
        ]
        return FakeResponse({"data": {"disease": {"id": "EFO_0001", "name": "Behcet", "associatedTargets": {"count": 3, "rows": rows}}}})


def test_opentargets_matches_disease_by_mesh_xref_not_first_hit():
    hit = opentargets.find_disease(FakeGraphQL(), "Behcet Syndrome", "MESH:D001528")
    assert hit == {"id": "EFO_0001", "name": "Behcet", "matched_by": "mesh"}


def test_opentargets_drops_known_drug_and_literature_evidence():
    out = opentargets.fetch(FakeGraphQL(), "Behcet Syndrome", "MESH:D001528")
    targets = out["targets"]
    assert targets["IL23R"] == {"genetic_association": 0.9}  # literatür puanı alınmadı
    assert "TNF" not in targets                               # yalnızca bilinen ilaç + literatür kanıtı vardı
    assert targets["ERAP1"] == {"animal_model": 0.4}          # sembol büyük harfe çevrilir, sıfır skor atılır
    assert out["n_genetic"] == 1
    assert set(out["excluded_types"]) == {"known_drug", "literature"}


def test_string_uses_only_experimental_and_database_scores():
    items = [
        {"preferredName_A": "TNF", "preferredName_B": "TNFRSF1A", "escore": 0.9, "dscore": 0.9, "tscore": 0.99},
        {"preferredName_A": "TNF", "preferredName_B": "TEXTONLY", "escore": 0.0, "dscore": 0.0, "tscore": 0.99},
        {"preferredName_A": "IL6", "preferredName_B": "IL6R", "escore": 0.5, "dscore": 0.0, "tscore": 0.0},
        {"preferredName_A": "IL6R", "preferredName_B": "IL6", "escore": 0.2, "dscore": 0.0, "tscore": 0.0},
    ]
    edges = string_ppi.parse_edges(items)
    assert ("TNF", "TEXTONLY") not in {(a, b) for a, b, _ in edges}  # yalnızca metin madenciliği: kenar değil
    assert ("IL6", "IL6R", 0.5) in edges                              # tekrarlar en yüksek puanla, sıralı uçlar
    assert dict(((a, b), s) for a, b, s in edges)[("TNF", "TNFRSF1A")] == round(1 - 0.1 * 0.1, 4)


def _toy_graph():
    """İki topluluk: hastalık modülü (S1..S5 + yakın hedef NEAR) ve uzak bir modül (FAR). HUB her yere bağlı."""
    edges = []
    module = ["S1", "S2", "S3", "S4", "S5", "NEAR"]
    for i, a in enumerate(module):
        for b in module[i + 1:]:
            edges.append((min(a, b), max(a, b), 0.9))
    far = [f"X{i}" for i in range(30)] + ["FAR"]
    for i, a in enumerate(far):
        for b in far[i + 1:i + 4]:
            edges.append((min(a, b), max(a, b), 0.9))
    for node in module + far:
        edges.append((min("HUB", node), max("HUB", node), 0.9))
    edges.append(("S1", "X0", 0.5))
    return sorted(set(edges))


def test_network_proximity_ranks_module_neighbour_above_far_node():
    z = network.proximity(_toy_graph(), {s: 1.0 for s in ("S1", "S2", "S3", "S4", "S5")}, n_perm=100)
    assert z["NEAR"] > 1
    assert z["NEAR"] > z["FAR"] and z["FAR"] < 0


def test_network_z_score_removes_hub_bias_of_raw_walk():
    """Ham yürüyüş olasılığı dereceyle güçlü ilişkili (merkez proteinler her hastalıkta öne çıkar); z-skoru değil."""
    rng = np.random.default_rng(1)
    edges = set()
    for i in range(1, 3000):
        for _ in range(3):
            j = int(rng.integers(0, i)) if rng.random() < 0.5 else int(rng.integers(0, max(1, min(i, 30))))
            if i != j:
                edges.add((f"G{min(i, j):05d}", f"G{max(i, j):05d}", 0.8))
    edges = sorted(edges)
    seeds = {f"G{k:05d}": 1.0 for k in rng.choice(np.arange(500, 3000), 40, replace=False)}
    z = network.proximity(edges, seeds, n_perm=100)
    g = network.Graph(edges)
    p0 = np.zeros(len(g))
    for s in seeds:
        p0[g.index[s]] = 1 / len(seeds)
    raw = g.propagate(p0)
    df = pd.DataFrame({"raw": raw, "z": [z[n] for n in g.names], "deg": g.degree}, index=g.names).drop(index=list(seeds))
    raw_corr = df["raw"].corr(df["deg"], method="spearman")
    z_corr = df["z"].corr(df["deg"], method="spearman")
    assert raw_corr > 0.3
    assert abs(z_corr) < raw_corr


def test_network_proximity_is_deterministic():
    seeds = {"S1": 1.0, "S2": 0.5}
    assert network.proximity(_toy_graph(), seeds, n_perm=50) == network.proximity(_toy_graph(), seeds, n_perm=50)


def test_seed_choice_prefers_genetic_genes_and_reports_fallback():
    genetic = {g: {"genetic_association": 0.5} for g in ("A", "B", "C", "D", "E")}
    seeds, source = network.choose_seeds(genetic, ["Z"])
    assert source == "genetik" and set(seeds) == {"A", "B", "C", "D", "E"}
    seeds, source = network.choose_seeds({"A": {"genetic_association": 0.5}}, ["z", "y"])
    assert source == "literatür köprü genleri" and set(seeds) == {"Y", "Z"}


def test_feature_table_reads_external_channels_through_drug_targets():
    ranked = pd.DataFrame([{"chemical_id": "D1", "score": 2.0, "lit_score": 2.0, "mech_score": 0.0, "penalty": 1.0}])
    drugs = {"CH1": {"mechanisms": [{"genes": ["il23r"]}]}, "CH2": {"mechanisms": [{"genes": ["JAK1"]}]}}
    external = {"genetic": {"IL23R": {"genetic_association": 0.9, "animal_model": 0.3}}, "network": {"JAK1": 3.2}}
    f = features.build(ranked, [], [], drugs, {"D1": "CH1", "D2": "CH2"}, external)
    assert list(f.index) == ["CH1", "CH2"]  # yalnızca bağımsız kanıtı olan ilaç da tabloya girer
    assert f.loc["CH1", "genetic"] == 0.9 and f.loc["CH1", "biology"] == 0.3
    assert f.loc["CH2", "network"] == 3.2 and f.loc["CH2", "lit"] == 0.0
    assert features.available_channels(f) == ["lit", "genetic", "biology", "network"]


def test_apply_to_candidates_keeps_method_score_and_reorders_only_when_decided():
    ranked = pd.DataFrame([
        {"chemical_id": "A", "chemical": "a", "score": 3.0, "lit_score": 3.0, "mech_score": 0.0, "penalty": 1.0},
        {"chemical_id": "B", "chemical": "b", "score": 2.0, "lit_score": 1.0, "mech_score": 1.0, "penalty": 1.0},
    ], index=pd.Index([1, 2], name="rank"))
    feats = pd.DataFrame({"base": [3.0, 2.0], "lit": [3.0, 1.0], "mech": [0.0, 1.0], "penalty": [1.0, 1.0],
                          "direct": [0.0, 2.0], "recent": [0.0, 1.0], "genetic": [0.0, 0.0], "biology": [0.0, 0.0],
                          "network": [0.0, 0.0], "prevalence": [0.1, 0.1], "n_targets": [0, 1]}, index=["A", "B"])
    same = fusion.apply_to_candidates(ranked, feats, {"method": "base"}, use_dict=False)
    assert list(same["chemical_id"]) == ["A", "B"] and list(same["score"]) == [3.0, 2.0]
    fused = fusion.apply_to_candidates(ranked, feats, {"method": "rrf", "channels": ["lit", "mech", "direct", "recent"]}, use_dict=False)
    assert list(fused["chemical_id"]) == ["B", "A"]  # B dört kanalda var, A yalnızca birinde
    assert list(fused.index) == [1, 2]
    assert fused.set_index("chemical_id").loc["A", "method_score"] == 3.0
    assert fused.set_index("chemical_id").loc["B", "n_channels"] == 4


def test_logistic_fusion_never_gives_negative_weight_to_evidence():
    rng = np.random.default_rng(0)
    groups, labels = [], []
    for g in range(3):
        n = 300
        f = pd.DataFrame({"lit": rng.random(n), "mech": rng.random(n), "prevalence": rng.random(n)}, index=[f"{g}_{i}" for i in range(n)])
        y = pd.Series(((f["lit"] > 0.9) | (f["prevalence"] < 0.03)).astype(float).to_numpy(), index=f.index)
        f["mech"] = 1 - f["lit"]  # kanıt kanalı hedefle ters ilişkili: katsayısı yine de < 0 olamaz
        groups.append(f)
        labels.append(y)
    m = fusion.LogisticFusion(["lit", "mech", "prevalence"]).fit(groups, labels)
    coef = dict(zip(m.names, m.coef))
    assert coef["lit"] > 0 and coef["mech"] >= 0
    assert coef["prevalence"] < 0  # denetim sütunu negatif olabilir
    assert np.allclose(fusion.LogisticFusion.from_dict(m.to_dict()).predict(groups[0]), m.predict(groups[0]), atol=1e-4)


def test_opentargets_name_variants_and_no_blind_fallback():
    assert opentargets.name_variants("Arthritis, Rheumatoid") == ["Rheumatoid Arthritis", "Arthritis, Rheumatoid"]
    assert opentargets.name_variants("Diabetes Mellitus, Type 2")[0] == "Type 2 Diabetes Mellitus"

    class Wrong:
        def request(self, method, url, json=None, **kwargs):
            q = json["query"]
            if "search(" in q:
                return FakeResponse({"data": {"search": {"hits": [{"id": "EFO_1001416", "name": "rheumatic fever nodule"}]}}})
            return FakeResponse({"data": {"disease": {"id": "EFO_1001416", "name": "rheumatic fever nodule", "dbXRefs": []}}})

    assert opentargets.find_disease(Wrong(), "Arthritis, Rheumatoid", "MESH:D001172") is None

    class ByName(Wrong):
        def request(self, method, url, json=None, **kwargs):
            if "search(" in json["query"]:
                return FakeResponse({"data": {"search": {"hits": [{"id": "EFO_0000685", "name": "rheumatoid arthritis"}]}}})
            return FakeResponse({"data": {"disease": {"id": "EFO_0000685", "name": "rheumatoid arthritis", "dbXRefs": []}}})

    assert opentargets.find_disease(ByName(), "Arthritis, Rheumatoid", "MESH:D001172")["matched_by"] == "ad"


def test_opentargets_falls_back_to_map_ids_and_reads_msh_prefix():
    """Serbest arama yalnızca alt tipleri döndürse bile asıl hastalık doğrudan eşlemeyle bulunur."""

    class Subtypes:
        def request(self, method, url, json=None, **kwargs):
            q = json["query"]
            if "search(" in q:
                return FakeResponse({"data": {"search": {"hits": [
                    {"id": "EFO_0003929", "name": "relapsing-remitting multiple sclerosis"},
                    {"id": "EFO_0003840", "name": "primary progressive multiple sclerosis"}]}}})
            if "mapIds" in q:
                return FakeResponse({"data": {"mapIds": {"mappings": [
                    {"term": "Multiple Sclerosis", "hits": [{"id": "MONDO_0005301", "name": "multiple sclerosis"}]}]}}})
            vid = json["variables"]["id"]
            xrefs = ["MSH:D009103"] if vid == "MONDO_0005301" else ["MSH:D020529"]
            return FakeResponse({"data": {"disease": {"id": vid, "name": "x", "dbXRefs": xrefs}}})

    hit = opentargets.find_disease(Subtypes(), "Multiple Sclerosis", "MESH:D009103")
    assert hit["id"] == "MONDO_0005301" and hit["matched_by"] == "mesh"


def test_string_skips_unmapped_genes_instead_of_failing_on_404():
    import requests

    class Partial:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, data=None, **kwargs):
            self.calls += 1
            ids = data["identifiers"].split("\r")
            if "BADGENE" in ids:  # STRING: gruptaki bir tanımlayıcı eşlenemediğinde 404
                resp = requests.Response()
                resp.status_code = 404
                raise requests.HTTPError(response=resp)
            return FakeResponse([{"preferredName_A": a, "preferredName_B": "STAT3", "escore": 0.9, "dscore": 0.0} for a in ids])

    edges = string_ppi.partners(Partial(), ["IL6", "TNF", "BADGENE"])
    assert {(a, b) for a, b, _ in edges} == {("IL6", "STAT3"), ("STAT3", "TNF")}
