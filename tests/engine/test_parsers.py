import json

import pandas as pd

from kesif_engine.extraction import drugmap
from kesif_engine.insights import novelty
from kesif_engine.sources import chembl, genes, pubmed
from kesif_engine.sources.pubtator import SCHEMA, parse_biocjson

DOC = {
    "id": "111",
    "passages": [
        {
            "infons": {"type": "title"},
            "annotations": [
                {"text": "TNF", "infons": {"type": "Gene", "identifier": "7124"}},
                {"text": "colchicine", "infons": {"type": "Chemical", "identifier": "MESH:D003078"}},
                {"text": "Behcet", "infons": {"type": "Disease", "identifier": "MESH:D001528"}},
                {"text": "x", "infons": {"type": "Species", "identifier": "9606"}},
            ],
        },
        {
            "infons": {"type": "abstract"},
            "annotations": [
                {"text": "TNF", "infons": {"type": "Gene", "identifier": "7124;7132"}},
                {"text": "unknown", "infons": {"type": "Chemical", "identifier": "-"}},
            ],
            "relations": [
                {"infons": {"type": "Negative_Correlation", "role1": "Chemical|MESH:D003078", "role2": "Gene|7124"}}
            ],
        },
    ],
    "relations": [
        {
            "id": "R1",
            "infons": {
                "type": "Positive_Correlation",
                "role1": {"type": "Gene", "identifier": "7124"},
                "role2": {"type": "Disease", "identifier": "MESH:D001528"},
            },
        }
    ],
}


def test_parse_annotations_and_relations():
    rec = parse_biocjson(json.dumps({"PubTator3": [DOC]}))["111"]
    assert rec["schema"] == SCHEMA
    assert rec["genes"] == {"7124": "TNF", "7132": "TNF"}
    assert rec["chemicals"] == {"MESH:D003078": "colchicine"}
    assert ["Positive_Correlation", "Gene", "7124", "Disease", "MESH:D001528"] in rec["relations"]
    assert ["Negative_Correlation", "Chemical", "MESH:D003078", "Gene", "7124"] in rec["relations"]


def test_parse_list_and_lines_and_garbage():
    assert set(parse_biocjson(json.dumps([DOC]))) == {"111"}
    lines = json.dumps(DOC) + "\n" + json.dumps({**DOC, "id": "222"})
    assert set(parse_biocjson(lines)) == {"111", "222"}
    assert parse_biocjson("") == {} and parse_biocjson("not json") == {}


def test_year_extraction():
    assert pubmed._year("2019 Mar 15") == 2019 and pubmed._year("") is None


def test_gene_summary_parse():
    result = {"uids": ["7124", "9"], "7124": {"name": "TNF", "description": "tumor necrosis factor", "organism": {"taxid": 9606}}, "9": {"error": "x"}}
    info = genes.parse_summary(result)
    assert info == {"7124": {"symbol": "TNF", "description": "tumor necrosis factor", "taxid": 9606}}
    assert genes.search_names("7124", "TNF-alpha", info) == ["TNF-alpha", "TNF", "tumor necrosis factor"]


def test_chembl_parse_and_assemble():
    mol = {
        "molecule_chembl_id": "CHEMBL1201580",
        "pref_name": "INFLIXIMAB",
        "max_phase": "4.0",
        "molecule_type": "Antibody",
        "molecule_synonyms": [{"molecule_synonym": "Remicade", "syn_type": "TRADE_NAME"}],
        "molecule_hierarchy": {"parent_chembl_id": "CHEMBL1201580"},
    }
    salt = {"molecule_chembl_id": "CHEMBLSALT", "pref_name": "DRUG X SODIUM", "max_phase": 2, "molecule_synonyms": None,
            "molecule_hierarchy": {"parent_chembl_id": "CHEMBLPARENT"}}
    mech = [{"molecule_chembl_id": "CHEMBL1201580", "target_chembl_id": "CHEMBL1825", "action_type": "Inhibitor", "mechanism_of_action": "TNF inhibitor"},
            {"molecule_chembl_id": "CHEMBLPARENT", "target_chembl_id": "CHEMBL1825", "action_type": "Inhibitor", "mechanism_of_action": "TNF inhibitor"}]
    target = {"target_chembl_id": "CHEMBL1825", "pref_name": "TNF-alpha",
              "target_components": [{"target_component_synonyms": [{"component_synonym": "TNF", "syn_type": "GENE_SYMBOL"}, {"component_synonym": "x", "syn_type": "UNIPROT"}]}]}
    targets = {"CHEMBL1825": {"name": "TNF-alpha", "genes": chembl.parse_target_genes(target)}}
    drugs = chembl.assemble([chembl.parse_molecule(mol), chembl.parse_molecule(salt)], mech, targets)
    by = {d["chembl_id"]: d for d in drugs}
    assert by["CHEMBL1201580"]["max_phase"] == 4.0 and by["CHEMBL1201580"]["synonyms"] == ["Remicade"]
    assert by["CHEMBL1201580"]["mechanisms"] == [{"action_type": "INHIBITOR", "moa": "TNF inhibitor", "target": "TNF-alpha", "genes": ["TNF"]}]
    assert by["CHEMBLSALT"]["mechanisms"][0]["genes"] == ["TNF"]  # ebeveynin mekanizması tuzlara da iner
    assert chembl._phase(None) == -1.0


def test_drug_map_matching_rules():
    drugs = [
        {"chembl_id": "C1", "name": "METHOTREXATE", "max_phase": 4.0, "synonyms": ["MTX", "Amethopterin"], "mechanisms": []},
        {"chembl_id": "C2", "name": "ADALIMUMAB", "max_phase": 4.0, "synonyms": ["Humira", "ADA"], "mechanisms": []},
        {"chembl_id": "C3", "name": "ONLYPHASE0", "max_phase": 0.5, "synonyms": [], "mechanisms": []},
    ]
    votes = {
        "M1": {"methotrexate": 30, "mtx": 5},
        "M2": {"mtx": 3},             # yalnızca kısaltma -> eşlenmez
        "M3": {"humira": 2},          # eş ad (>=5 karakter) -> eşlenir
        "M4": {"ada": 4},             # kısa eş ad -> eşlenmez
        "M5": {"onlyphase0": 1},      # faz eşiğinin altında -> eşlenmez
        "M6": {"steroid": 9},
        "M7": {"h&e": 7, "humira": 1},  # '&' içeren etiket atlanır, sonraki etiket denenir
        "M8": {"atp": 50},            # endojen metabolit -> eşlenmez
    }
    drugs.append({"chembl_id": "C4", "name": "ADENOSINE TRIPHOSPHATE", "max_phase": 2.0, "synonyms": ["ATP"], "mechanisms": []})
    m = drugmap.build_map(votes, drugs, manual={"M6": "C2", "M3": None}, min_phase=1.0)
    assert m == {"M1": "C1", "M6": "C2", "M7": "C2"}


def test_novelty_status_logic(monkeypatch):
    monkeypatch.setattr(novelty, "trials_count", lambda c, cond, intr: {"a": 2}.get(intr, 0))
    monkeypatch.setattr(novelty, "pubmed_direct_count", lambda c, q, names: {"b": 5}.get(names[0], 0))
    df = pd.DataFrame({"chemical": ["a", "b", "c"], "score": [3.0, 2.0, 1.0], "aliases": ["", "bb;x", ""]})
    out = novelty.add_novelty(None, df, {"name_en": "X", "pubmed_query": "X"}, 10)
    assert list(out["durum"]) == ["klinik çalışma kaydı var", "literatürde doğrudan geçiyor", "doğrudan kayıt yok (aday)"]


def test_annotate_isolates_pmids_pubtator_has_not_processed():
    """Çok yeni PMID içeren grup 400 döndürür; grup bölünerek yalnızca o makale 'bekliyor' kaydedilir."""
    import requests

    from kesif_engine.sources import pubtator

    class Resp:
        def __init__(self, code, text=""):
            self.status_code, self.text = code, text

    class Client:
        calls = 0

        def request(self, method, url, params=None, **kw):
            Client.calls += 1
            ids = params["pmids"].split(",")
            if "999" in ids:
                err = requests.HTTPError("400")
                err.response = Resp(400)
                raise err
            docs = [{"pmid": p, "passages": []} for p in ids]
            return Resp(200, json.dumps(docs))

    out = pubtator.annotate(Client(), ["1", "2", "999", "4"])
    assert out["999"]["schema"] == 0 and out["1"]["schema"] == pubtator.SCHEMA and set(out) == {"1", "2", "999", "4"}
