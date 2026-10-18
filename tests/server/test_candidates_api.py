from __future__ import annotations

import pytest

from tests.server.conftest import make_rows


@pytest.fixture
def loaded(client, admin_headers, fakes, container):
    fakes["reader"].rows["behcet"] = make_rows()
    container.sync_results("behcet", None)
    return admin_headers


def _get(client, h, **params):
    r = client.get("/api/v1/diseases/behcet/candidates", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_default_hides_endogenous_and_pages(client, loaded):
    body = _get(client, loaded)
    assert body["page"]["total"] == 4
    assert [c["chemical"] for c in body["items"]] == ["drug0", "drug1", "drug3", "drug4"]
    body = _get(client, loaded, show_endogenous="true", limit=2, offset=1)
    assert body["page"]["total"] == 5 and [c["rank"] for c in body["items"]] == [2, 3]


def test_filters(client, loaded):
    assert _get(client, loaded, status="yeni")["page"]["total"] == 2
    assert _get(client, loaded, kind="besin/takviye")["page"]["total"] == 1
    assert _get(client, loaded, min_phase=4)["page"]["total"] == 2
    assert _get(client, loaded, exclude_known="true")["page"]["total"] == 1
    assert _get(client, loaded, q="ALIAS3")["items"][0]["chemical"] == "drug3"
    assert _get(client, loaded, q="tnf")["page"]["total"] == 4
    assert _get(client, loaded, direction="tutarlı")["page"]["total"] == 2
    assert _get(client, loaded, min_score=9)["page"]["total"] == 2


def test_sorting(client, loaded):
    items = _get(client, loaded, sort="chemical")["items"]
    assert [c["chemical"] for c in items] == ["drug0", "drug1", "drug3", "drug4"]
    items = _get(client, loaded, sort="score", order="asc")["items"]
    assert items[0]["chemical"] == "drug4"
    assert client.get("/api/v1/diseases/behcet/candidates?sort=bogus", headers=loaded).status_code == 422


def test_nan_in_extra_becomes_null(client, loaded):
    c = _get(client, loaded, q="drug0")["items"][0]
    assert c["extra"]["extra_col"] is None


def test_detail_and_evidence(client, loaded):
    r = client.get("/api/v1/diseases/behcet/candidates/MESH:D1", headers=loaded)
    assert r.status_code == 200 and r.json()["candidate"]["chemical"] == "drug1" and r.json()["evidence"][0]["pmid"] == "1"
    assert client.get("/api/v1/diseases/behcet/candidates/MESH:D99", headers=loaded).status_code == 404
    assert client.get("/api/v1/diseases/behcet/report", headers=loaded).status_code == 404


def test_export_csv_from_db(client, loaded):
    r = client.get("/api/v1/diseases/behcet/export.csv", headers=loaded)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("rank,chemical_id,chemical") and "extra_col" in lines[0]
    assert len(lines) == 6  # başlık + 5 (endojen dahil)
    assert client.get("/api/v1/diseases/fmf/export.csv", headers=loaded).status_code == 404


def test_resync_replaces(client, loaded, fakes, container):
    fakes["reader"].rows["behcet"] = make_rows(2)
    assert container.sync_results("behcet", "run1")["candidates"] == 2
    body = _get(client, loaded, show_endogenous="true")
    assert body["page"]["total"] == 2


def test_artifacts_served_from_db(client, admin_headers, fakes, container):
    fakes["reader"].rows["behcet"] = make_rows(1)
    fakes["reader"].extras["behcet"] = {
        "known": [{"drug": "colchicine"}], "backtest": {"cutoff_year": 2018}, "tuning": {"verdict": "ok"},
        "bridges": [{"gene_id": "1", "label": "TNF", "n_articles": 5, "source": "direct", "used": True, "has_literature": True, "n_literature": 3}],
        "discovery": {"n_new": 4, "top_overall": []}, "report": "<html>rapor</html>",
    }
    fakes["engine"].corpus = True
    container.sync_results("behcet", "r1")
    fakes["reader"].extras["behcet"] = {}  # dosyalar silinse bile veritabanından okunur
    fakes["reader"].rows["behcet"] = []
    h = admin_headers
    assert client.get("/api/v1/diseases/behcet/known", headers=h).json() == [{"drug": "colchicine"}]
    assert client.get("/api/v1/diseases/behcet/backtest", headers=h).json()["cutoff_year"] == 2018
    assert client.get("/api/v1/diseases/behcet/tuning", headers=h).json()["verdict"] == "ok"
    assert client.get("/api/v1/diseases/behcet/bridges", headers=h).json()[0]["symbol"] == ""
    assert client.get("/api/v1/diseases/behcet/discovery", headers=h).json()["n_new"] == 4
    assert client.get("/api/v1/diseases/behcet/report", headers=h).text == "<html>rapor</html>"
    assert client.get("/api/v1/compare", headers=h).json() == [{"key": "behcet", "label": "Behçet hastalığı", "n_new": 4}]
    st = client.get("/api/v1/diseases/behcet", headers=h).json()["status"]
    assert st["has_corpus"] is True and st["n_articles"] == 123
    # kalıcı silme çıktıları da siler
    assert client.delete("/api/v1/diseases/behcet?purge=true", headers=h).status_code == 200
