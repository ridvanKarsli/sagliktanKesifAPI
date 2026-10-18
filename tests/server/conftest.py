"""Sunucu testleri: bellek içi SQLite, sahte motor, eşzamanlı kuyruk. Dış ağ yok."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from kesif_server.container import Container
from kesif_server.core.settings import Settings
from kesif_server.domain.entities import Disease, RunProgress
from kesif_server.infrastructure.db.models import Base
from kesif_server.infrastructure.queue.inprocess import SynchronousQueue
from kesif_server.interfaces.api.app import create_app


class FakeEngine:
    """AnalysisEngine port'unun sahtesi: adımları 'çalıştırır', ilerleme yayar, isteğe bağlı hata fırlatır."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.fail_with: Exception = None
        self.fail_times = 0
        self.corpus = False

    def run(self, disease, steps, params, options, progress, log_path: Path):
        self.calls.append({"disease": disease.key, "steps": steps, "params": params, "options": options})
        if self.fail_with and self.fail_times > 0:
            self.fail_times -= 1
            raise self.fail_with
        for s in steps:
            progress(RunProgress(step=s, message=f"{s} çalışıyor", completed_steps=steps[: steps.index(s)]))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ok\n", encoding="utf-8")
        return {s: {"ok": True} for s in steps}

    def rerank(self, disease, params, cutoff):
        return {"n_candidates": 3, "cutoff": cutoff}

    def default_params(self):
        return {"top_k": 30, "min_spec": 1.5}

    def validate_params(self, params):
        from kesif_server.domain.errors import ValidationError

        unknown = set(params) - {"top_k", "min_spec"}
        if unknown:
            raise ValidationError(f"Bilinmeyen parametre: {unknown}")
        return {**self.default_params(), **params}

    def status(self, key):
        return {"disease": key, "has_corpus": bool(self.corpus), "n_articles": 123 if self.corpus else 0, "steps": {}}

    def is_network_error(self, exc):
        return isinstance(exc, ConnectionError)


class FakeReader:
    """Motorun çalışma dizinini taklit eder: testler `rows`/`extras` sözlüklerini doldurur."""

    def __init__(self):
        self.rows: Dict[str, List[Dict[str, Any]]] = {}
        self.extras: Dict[str, Dict[str, Any]] = {}

    def _x(self, key, name, default):
        return self.extras.get(key, {}).get(name, default)

    def candidates(self, key):
        return self.rows.get(key, [])

    def evidence(self, key, cid):
        return [{"pmid": "1", "chemical_id": cid}]

    def known_therapies(self, key):
        return self._x(key, "known", [])

    def backtest(self, key):
        return self._x(key, "backtest", {})

    def tuning(self, key):
        return self._x(key, "tuning", {})

    def bridges(self, key):
        return self._x(key, "bridges", [])

    def discovery(self, key):
        return self._x(key, "discovery", {})

    def compare_row(self, key, label, summary):
        return {"key": key, "label": label, "n_new": summary.get("n_new")}

    def report_html(self, key):
        return self._x(key, "report", None)

    def log_tail(self, key, tail):
        return []


class FakeCatalog:
    def status(self):
        return {"ready": False}

    def build(self, progress):
        progress("x")

    def search(self, q, limit):
        return []

    def browse(self, q, c, o, l):
        return {"items": [], "total": 0}

    def article_count(self, term, entries):
        return {"count": 1, "query": term}

    def resolve(self, name):
        return []

    def build_query(self, mesh_term, name_en):
        return f'"{mesh_term or name_en}"[MeSH Terms]'

    def slugify(self, text):
        import re

        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class FakeServices:
    def health(self):
        return [{"key": "x", "name": "X", "ok": True, "ms": 1, "detail": ""}]

    def drug_indications(self, cid):
        return [{"term": "t", "phase": 4}]

    def pubmed_count(self, q):
        return 42

    def estimate(self, disease, max_a):
        return {"pubmed_total": 42}


def make_rows(n=5):
    kinds = ["ilaç", "ilaç", "endojen", "besin/takviye", "ilaç"]
    statuses = ["yeni", "literatürde anılmış", "yeni", "yeni", "tedavi olarak çalışılmış"]
    return [{"rank": i + 1, "chemical_id": f"MESH:D{i}", "chemical": f"drug{i}", "chembl_id": f"CHEMBL{i}", "score": 10 - i,
             "lit_score": 5.0, "mech_score": 1.0, "direction": "tutarlı" if i % 2 else "", "mechanism": "TNF inhibitor",
             "max_phase": 4 if i < 3 else 2, "n_direct_a": i, "n_bridges": 3, "top_bridges": "TNF, IL6", "aliases": f"alias{i}",
             "kind": kinds[i], "status": statuses[i], "harm_signal": "yok", "durum": "", "extra_col": float("nan") if i == 0 else i}
            for i in range(n)]


class RecordingWorkspace:
    """WorkspaceSync sahtesi: çağrıları kaydeder."""

    def __init__(self):
        self.calls: List[tuple] = []

    def prepare(self, disease_key=None):
        self.calls.append(("prepare", disease_key))
        return {"downloaded": 0, "up_to_date": 0}

    def save(self, disease_key=None):
        self.calls.append(("save", disease_key))
        return {"uploaded": 0, "unchanged": 0, "deleted": 0}

    def discard(self, disease_key):
        self.calls.append(("discard", disease_key))
        return 3


@pytest.fixture
def settings(tmp_path):
    return Settings(env="test", database_url="sqlite+pysqlite:///:memory:", work_dir=tmp_path / "work",
                    auth_mode="local", bootstrap_admin_email="admin@test.com", bootstrap_admin_password="secretpass123",
                    rate_limit_per_minute=0, log_level="WARNING")


@pytest.fixture
def fakes():
    return {"engine": FakeEngine(), "reader": FakeReader(), "catalog": FakeCatalog(), "services": FakeServices(), "workspace": RecordingWorkspace()}


@pytest.fixture
def container(settings, fakes):
    holder = {}
    c = Container(settings, engine=fakes["engine"], reader=fakes["reader"], catalog_port=fakes["catalog"], services=fakes["services"],
                  queue=SynchronousQueue(lambda rid: holder["c"].execute_run(rid)), workspace=fakes["workspace"])
    holder["c"] = c
    Base.metadata.create_all(c.db_engine)
    c.execute_run._retry_delay = 0
    c.startup()
    return c


@pytest.fixture
def client(container):
    app = create_app(container)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
def admin_headers(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@test.com", "password": "secretpass123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture
def viewer_headers(client, admin_headers):
    r = client.post("/api/v1/users", headers=admin_headers, json={"email": "v@test.com", "password": "viewerpass123", "role": "viewer"})
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/auth/login", json={"email": "v@test.com", "password": "viewerpass123"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture
def disease(container):
    with container.uow() as uow:
        d = uow.diseases.get("behcet") or uow.diseases.add(Disease(key="behcet", label="Behçet", name_en="Behcet", pubmed_query="q"))
        uow.commit()
        return d
