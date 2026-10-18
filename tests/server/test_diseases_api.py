from __future__ import annotations


def test_seeded_defaults(client, admin_headers):
    r = client.get("/api/v1/diseases", headers=admin_headers)
    assert r.status_code == 200
    keys = {d["key"] for d in r.json()}
    assert {"behcet", "fmf"} <= keys
    assert all(d["is_default"] for d in r.json())


def test_add_get_remove(client, admin_headers):
    r = client.post("/api/v1/diseases", headers=admin_headers, json={"name_en": "Gout", "mesh_term": "Gout", "mesh_id": "MESH:D006073"})
    assert r.status_code == 201, r.text
    assert r.json()["key"] == "gout" and "Gout" in r.json()["pubmed_query"]
    r = client.post("/api/v1/diseases", headers=admin_headers, json={"name_en": "Gout"})
    assert r.status_code == 409
    r = client.get("/api/v1/diseases/gout", headers=admin_headers)
    assert r.status_code == 200 and r.json()["active_run"] is None and r.json()["n_candidates"] == 0
    r = client.delete("/api/v1/diseases/gout", headers=admin_headers)
    assert r.status_code == 200 and r.json()["purged"] == []
    assert client.get("/api/v1/diseases/gout", headers=admin_headers).status_code == 404
    # kaldırılan yeniden eklenebilir
    r = client.post("/api/v1/diseases", headers=admin_headers, json={"name_en": "Gout", "label": "Gut"})
    assert r.status_code == 201 and r.json()["label"] == "Gut"


def test_purge_requires_admin(client, admin_headers, viewer_headers, container):
    from kesif_server.domain.entities import Role

    with container.uow() as uow:
        v = uow.users.get_by_email("v@test.com")
        v.role = Role.ANALYST
        uow.users.update(v)
        uow.commit()
    r = client.delete("/api/v1/diseases/behcet?purge=true", headers=viewer_headers)
    assert r.status_code == 403
    r = client.delete("/api/v1/diseases/behcet?purge=true", headers=admin_headers)
    assert r.status_code == 200 and r.json()["purged"] == ["candidates:0", "artifacts:0", "files:3"]


def test_default_disease_can_be_removed_and_is_not_reseeded(client, admin_headers, container):
    assert client.delete("/api/v1/diseases/fmf", headers=admin_headers).status_code == 200
    container.seed_diseases()
    assert client.get("/api/v1/diseases/fmf", headers=admin_headers).status_code == 404


def test_settings_roundtrip(client, admin_headers):
    r = client.get("/api/v1/diseases/behcet/settings", headers=admin_headers)
    assert r.status_code == 200 and r.json()["defaults"]["top_k"] == 30
    r = client.put("/api/v1/diseases/behcet/settings", headers=admin_headers, json={"top_k": 50})
    assert r.status_code == 200 and r.json()["top_k"] == 50
    assert client.get("/api/v1/diseases/behcet/settings", headers=admin_headers).json()["params"]["top_k"] == 50
    assert client.get("/api/v1/diseases/behcet", headers=admin_headers).json()["status"]["settings"]["top_k"] == 50
    r = client.put("/api/v1/diseases/behcet/settings", headers=admin_headers, json={"bogus": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_compare_and_services(client, admin_headers):
    assert client.get("/api/v1/compare", headers=admin_headers).status_code == 200
    r = client.get("/api/v1/health/services", headers=admin_headers)
    assert r.json()["ok"] is True
    r = client.get("/api/v1/drugs/CHEMBL1/indications", headers=admin_headers)
    assert r.json()["indications"][0]["term"] == "t"
    assert client.get("/api/v1/drugs/nope/indications", headers=admin_headers).status_code == 422
    assert client.get("/api/v1/diseases/behcet/pubmed-count", headers=admin_headers).json()["count"] == 42


def test_catalog_not_ready(client, admin_headers):
    r = client.get("/api/v1/catalog/search?q=beh", headers=admin_headers)
    assert r.status_code == 409
    r = client.post("/api/v1/catalog/resolve", headers=admin_headers, json={"name": "x"})
    assert r.status_code == 422  # min_length


def test_reserved_keys_cannot_become_diseases(client, admin_headers):
    r = client.post("/api/v1/diseases", headers=admin_headers, json={"name_en": "Benchmark"})
    assert r.status_code == 422 and "sistem" in r.json()["error"]["message"]


def test_benchmark_falls_back_to_packaged_decision_then_reads_stored_scorecard(client, admin_headers, container):
    import json

    from kesif_server.domain.entities import StoredFile

    r = client.get("/api/v1/benchmark", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["benchmark"] is None and r.json()["decision"]["origin"] == "paketle gelen ölçüm"

    payload = {"n_groups": 9, "decision": {"method": "rrf", "verdict": "benimsendi"}}
    with container.uow() as uow:
        for path, body in (("data/benchmark/benchmark.json", payload), ("data/benchmark/fusion_model.json", payload["decision"])):
            data = json.dumps(body).encode()
            uow.files.put(StoredFile(path=path, scope="shared", sha256="x", size=len(data), content=data))
        uow.commit()
    r = client.get("/api/v1/benchmark", headers=admin_headers)
    assert r.json()["benchmark"]["n_groups"] == 9 and r.json()["decision"]["verdict"] == "benimsendi"
