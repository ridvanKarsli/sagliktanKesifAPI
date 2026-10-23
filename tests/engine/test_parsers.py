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


def test_annotate_skips_newest_tail_once_pubtator_lag_is_found():
    """En yeni makaleler henüz işlenmemiş: ilk bekleyen bulununca daha yeni PMID'ler sorulmadan beklemeye alınır;
    tek bir eski bekleyen makale ise eşik kurmaz."""
    import requests

    from kesif_engine.sources import pubtator

    processed_up_to = 5_000_100
    odd_old = "4000000"

    class Resp:
        def __init__(self, code, text=""):
            self.status_code, self.text = code, text

    class Client:
        asked: list = []

        def request(self, method, url, params=None, **kw):
            ids = params["pmids"].split(",")
            Client.asked.extend(ids)
            if any(int(p) > processed_up_to or p == odd_old for p in ids):
                err = requests.HTTPError("400")
                err.response = Resp(400)
                raise err
            return Resp(200, json.dumps([{"pmid": p, "passages": []} for p in ids]))

    pubtator.reset_pending_floor()
    pmids = [odd_old] + [str(5_000_000 + i) for i in range(400)]
    out = pubtator.annotate(Client(), pmids)
    assert out[odd_old]["schema"] == 0
    assert out["5000050"]["schema"] == pubtator.SCHEMA  # eski bekleyen makale eşik kurmadı
    assert all(out[str(5_000_000 + i)]["schema"] == 0 for i in range(101, 400))
    asked_new = {p for p in Client.asked if int(p) > processed_up_to + 100}
    assert not asked_new, "eşikten sonraki PMID'ler sorulmamalıydı"
    pubtator.reset_pending_floor()


def test_data_until_and_year_search_respect_the_freeze_date():
    import datetime

    from kesif_engine.sources import pubmed

    assert pubmed.data_until(datetime.date(2026, 10, 20)) == "2026/09/20"

    class Resp:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    class Client:
        calls: list = []

        def request(self, method, url, params=None, **kw):
            Client.calls.append((params.get("mindate"), params.get("maxdate")))
            return Resp({"esearchresult": {"idlist": [f"{params.get('mindate')}"], "count": "1"}})

    ids = pubmed.search_by_year(Client(), "x", 2025, 2027, until="2026/09/20")
    assert ("2026/01/01", "2026/09/20") in Client.calls
    assert all(lo[:4] != "2027" for lo, _ in Client.calls if lo)  # kesim yılından sonrası hiç aranmaz
    assert len(ids) == 2


def test_clinicaltrials_count_tolerates_control_characters():
    from kesif_engine.sources import clinicaltrials

    class Resp:
        text = '{"totalCount": 7, "note": "satır\x01sonu"}'

    class Client:
        def request(self, *a, **k):
            return Resp()

    assert clinicaltrials.count(Client(), "x", "y") == 7


def test_novelty_survives_a_single_failing_candidate():
    import pandas as pd

    from kesif_engine.insights import novelty

    class Resp:
        def __init__(self, payload):
            self._p, self.text = payload, None

        def json(self):
            return self._p

    class Client:
        def request(self, method, url, params=None, **kw):
            if "clinicaltrials" in url:
                return Resp({"totalCount": 2})
            if "bad" in params.get("term", ""):
                return Resp({"esearchresult": {"ERROR": "Invalid query"}})
            return Resp({"esearchresult": {"count": "5"}})

    df = pd.DataFrame([{"chemical": "good", "aliases": ""}, {"chemical": 'bad "name', "aliases": ""}])
    out = novelty.add_novelty(Client(), df, {"name_en": "X", "pubmed_query": "x"}, top_n=2)
    assert out["pubmed_direct"].iloc[0] == 5 and pd.isna(out["pubmed_direct"].iloc[1]) and list(out["durum"])[1] == ""


def test_pubmed_json_tolerates_control_characters():
    from kesif_engine.sources import pubmed

    class R:
        text = '{"esearchresult": {"count": "2", "idlist": ["1", "2"], "querytranslation": "a\tb\x01"}}'

    assert pubmed._json(R())["esearchresult"]["idlist"] == ["1", "2"]
