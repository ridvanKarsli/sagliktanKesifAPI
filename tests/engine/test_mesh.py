from kesif_engine.catalog import mesh

SAMPLE = """*NEWRECORD
RECTYPE = D
MH = Behcet Syndrome
ENTRY = Behcet Disease|T047|NON|EQV|NLM (1998)|980312|abbcdef
ENTRY = Behcet's Syndrome
PRINT ENTRY = Adamantiades-Behcet Disease
MN = C07.465.075
MN = C14.907.940.100
UI = D001528

*NEWRECORD
RECTYPE = D
MH = Familial Mediterranean Fever
ENTRY = Periodic Disease
MN = C16.320.382
UI = D005258

*NEWRECORD
RECTYPE = D
MH = Aspirin
MN = D02.455.426.559.389.657.410.595.100
UI = D001241

*NEWRECORD
RECTYPE = D
MH = Depressive Disorder
MN = F03.600.300
UI = D003866
"""


def test_parse_keeps_only_disease_branches():
    items = mesh.parse_ascii(SAMPLE)
    terms = [i["term"] for i in items]
    assert terms == ["Behcet Syndrome", "Familial Mediterranean Fever", "Depressive Disorder"]
    b = items[0]
    assert b["ui"] == "D001528"
    assert "Behcet Disease" in b["entries"] and "Adamantiades-Behcet Disease" in b["entries"]
    assert all("|" not in e for e in b["entries"])


def test_search_is_accent_and_case_insensitive(tmp_path, monkeypatch):
    import json

    from kesif_engine.core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    mesh._cache.clear()
    mesh.catalog_path().write_text(json.dumps({"year": 2026, "n": 3, "items": mesh.parse_ascii(SAMPLE)}), encoding="utf-8")
    for q in ("behçet", "BEHCET", "Behçet hastalığı", "adamantiades"):
        r = mesh.search(q)
        assert r and r[0]["term"] == "Behcet Syndrome", q
    assert mesh.search("ailesel akdeniz") == []  # Türkçe karşılık MeSH'te yok; İngilizce ad gerekir
    assert mesh.search("mediterranean")[0]["mesh_id"] == "MESH:D005258"
    assert mesh.search("xx") == []
    assert mesh.status()["n"] == 3 and mesh.status()["ready"] is False  # 1000 kaydın altı "hazır" sayılmaz


def test_browse_filters_by_category_and_query(tmp_path, monkeypatch):
    import json

    from kesif_engine.core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    mesh._cache.clear()
    mesh.catalog_path().write_text(json.dumps({"year": 2026, "n": 3, "items": mesh.parse_ascii(SAMPLE)}), encoding="utf-8")
    all_ = mesh.browse()
    assert all_["total"] == 3 and [i["term"] for i in all_["items"]][0] == "Behcet Syndrome"
    assert {c["code"] for c in all_["categories"]} >= {"C07", "C14", "C16", "F03"}
    mental = mesh.browse(category="F03")
    assert [i["term"] for i in mental["items"]] == ["Depressive Disorder"]
    assert mesh.browse(query="mediterr")["items"][0]["mesh_id"] == "MESH:D005258"
    assert mesh.browse(query="aspirin")["total"] == 0  # ilaç, hastalık değil


def test_build_rejects_html_and_empty_catalogs(tmp_path, monkeypatch):
    """NLM beklenmeyen içerik döndürürse boş katalog yazılmamalı; SPARQL yedeği de boşsa hata verilmeli."""
    import pytest

    from kesif_engine.core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    mesh._cache.clear()

    class R:
        def __init__(self, text="", data=None):
            self.text = text
            self._data = data or {}

        def json(self):
            return self._data

    class FakeClient:
        def request(self, method, url, params=None, **kw):
            if "sparql" in url:
                return R(data={"results": {"bindings": []}})
            return R(text="<html><body>Not the file</body></html>")

    with pytest.raises(RuntimeError):
        mesh.build(FakeClient())
    assert not mesh.catalog_path().exists()
    assert (tmp_path / "mesh" / "last_response_head.txt").exists()
    assert mesh.status()["ready"] is False


def test_sparql_fallback_builds_catalog():
    class R:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    class FakeClient:
        def request(self, method, url, params=None, **kw):
            q = params["query"]
            if "?syn" in q:
                rows = [{"d": {"value": "http://id.nlm.nih.gov/mesh/D001528"}, "syn": {"value": "Behcet Disease"}},
                        {"d": {"value": "http://id.nlm.nih.gov/mesh/D001528"}, "syn": {"value": "Behcet Syndrome"}}]
            else:
                rows = [{"d": {"value": "http://id.nlm.nih.gov/mesh/D001528"}, "label": {"value": "Behcet Syndrome"}, "tn": {"value": "C07.465.075"}},
                        {"d": {"value": "http://id.nlm.nih.gov/mesh/D001528"}, "label": {"value": "Behcet Syndrome"}, "tn": {"value": "C14.907.940.100"}}]
            return R({"results": {"bindings": rows if "OFFSET 0" in q else []}})

    items = mesh.build_from_sparql(FakeClient())
    assert items == [{"ui": "D001528", "term": "Behcet Syndrome", "entries": ["Behcet Disease"], "trees": ["C07.465.075", "C14.907.940.100"]}]


def test_pubmed_query_uses_synonyms():
    q = mesh.pubmed_query("Behcet Syndrome", ["Behcet Disease", "Behcet's Syndrome"])
    assert q.startswith('"Behcet Syndrome"[MeSH Terms] OR ')
    assert '"Behcet Disease"[Title/Abstract]' in q
