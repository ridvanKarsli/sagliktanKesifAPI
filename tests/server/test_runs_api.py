from __future__ import annotations

from tests.server.conftest import make_rows


def test_run_lifecycle_imports_candidates(client, admin_headers, fakes):
    fakes["reader"].rows["behcet"] = make_rows()
    r = client.post("/api/v1/diseases/behcet/runs", headers=admin_headers, json={"steps": ["rank", "fetch"], "top": 50})
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["steps"] == ["fetch", "rank"]  # motor sırası
    r = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers)
    body = r.json()
    assert body["status"] == "done"
    assert body["summary"]["imported"]["candidates"] == 5
    assert body["progress"]["completed_steps"] == ["fetch", "rank"]
    assert fakes["engine"].calls[0]["options"]["top"] == 50
    # çalışma dizini: önce veritabanından hazırlanır, sonra geri yazılır
    assert [c for c in fakes["workspace"].calls if c[1] == "behcet"] == [("prepare", "behcet"), ("save", "behcet")]
    r = client.get("/api/v1/diseases/behcet", headers=admin_headers)
    assert r.json()["n_candidates"] == 5
    r = client.get("/api/v1/diseases/behcet/log", headers=admin_headers)
    assert r.json()["lines"] == ["ok"]  # iş günlüğü veritabanında
    st = client.get("/api/v1/diseases/behcet", headers=admin_headers).json()["status"]
    assert st["last_job"]["status"] == "done" and st["last_job"]["steps"] == ["fetch", "rank"]


def test_unknown_step_and_bad_params(client, admin_headers):
    r = client.post("/api/v1/diseases/behcet/runs", headers=admin_headers, json={"steps": ["bogus"]})
    assert r.status_code == 422
    r = client.post("/api/v1/diseases/behcet/runs", headers=admin_headers, json={"steps": ["rank"], "params": {"nope": 1}})
    assert r.status_code == 422


def test_failed_run_records_error(client, admin_headers, fakes):
    fakes["engine"].fail_with = RuntimeError("boom")
    fakes["engine"].fail_times = 5
    r = client.post("/api/v1/diseases/behcet/runs", headers=admin_headers, json={"steps": ["rank"]})
    run = client.get(f"/api/v1/runs/{r.json()['id']}", headers=admin_headers).json()
    assert run["status"] == "failed" and "boom" in run["error"]
    assert run["attempts"] == 1
    st = client.get("/api/v1/diseases/behcet", headers=admin_headers).json()["status"]
    assert st["last_job"]["status"] == "failed"


def test_network_error_retries_once(client, admin_headers, fakes):
    fakes["engine"].fail_with = ConnectionError("timeout")
    fakes["engine"].fail_times = 1
    r = client.post("/api/v1/diseases/behcet/runs", headers=admin_headers, json={"steps": ["rank"]})
    run = client.get(f"/api/v1/runs/{r.json()['id']}", headers=admin_headers).json()
    assert run["status"] == "done"
    assert len(fakes["engine"].calls) == 2


def test_cancel_only_queued(client, admin_headers):
    r = client.post("/api/v1/diseases/behcet/runs", headers=admin_headers, json={"steps": ["rank"]})
    rid = r.json()["id"]
    r = client.delete(f"/api/v1/runs/{rid}", headers=admin_headers)
    assert r.status_code == 409  # eşzamanlı kuyrukta iş çoktan bitti


def test_rerank(client, admin_headers, fakes):
    fakes["reader"].rows["behcet"] = make_rows(2)
    r = client.post("/api/v1/diseases/behcet/rerank", headers=admin_headers, json={"params": {"top_k": 10}, "cutoff": 2018})
    assert r.status_code == 200 and r.json()["imported"]["candidates"] == 2 and r.json()["cutoff"] == 2018
    # yeniden sıralama parametreleri hastalığa kaydeder
    assert client.get("/api/v1/diseases/behcet/settings", headers=admin_headers).json()["params"]["top_k"] == 10


def test_recover_requeues_running(container, admin_headers, client, fakes):
    """Süreç çökmesinde 'running' kalan iş yeniden sıraya alınır ve (eşzamanlı kuyrukta hemen) yeniden çalışır."""
    from kesif_server.domain.entities import AnalysisRun, AnalysisStep, RunOptions, RunStatus

    with container.uow() as uow:
        run = AnalysisRun(disease_key="behcet", steps=[AnalysisStep.RANK], params={}, options=RunOptions(), status=RunStatus.RUNNING)
        uow.runs.add(run)
        uow.commit()
    assert fakes["engine"].calls == []
    container.recover_runs()
    body = client.get(f"/api/v1/runs/{run.id}", headers=admin_headers).json()
    assert body["status"] == "done" and len(fakes["engine"].calls) == 1 and body["attempts"] == 1
