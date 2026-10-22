"""SQLite → başka veritabanı kopyalama ve doğrulama."""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest
from sqlalchemy import insert, select, update

from kesif_server.infrastructure.db.copy import copy_database, verify
from kesif_server.infrastructure.db.models import Base
from kesif_server.infrastructure.db.session import make_engine


def _filled(url: str):
    e = make_engine(url)
    Base.metadata.create_all(e)
    t = Base.metadata.tables
    now = datetime(2026, 10, 22, 12, 0, 0)
    blob = b"x" * 300_000
    with e.begin() as c:
        c.execute(insert(t["users"]), [{"id": "u1", "email": "a@b.c", "display_name": "A", "role": "admin",
                                         "is_active": True, "created_at": now}])
        c.execute(insert(t["diseases"]), [{"key": "behcet", "label": "Behçet", "name_en": "Behcet", "mesh_id": "MESH:D001528",
                                            "pubmed_query": "q", "is_default": True, "is_removed": False, "params": {"k": [1, 2]},
                                            "created_by": "u1", "created_at": now, "updated_at": now}])
        c.execute(insert(t["candidates"]), [{"disease_key": "behcet", "rank": i, "chemical_id": f"C{i}", "chemical": f"ilaç {i}",
                                              "score": 1.0 / i, "extra": {"accel_score": i}} for i in range(1, 51)])
        c.execute(insert(t["engine_files"]), [{"path": f"data/behcet/f{i}", "scope": "behcet", "sha256": hashlib.sha256(blob + bytes([i])).hexdigest(),
                                                "size": len(blob) + 1, "content": blob + bytes([i]), "updated_at": now} for i in range(5)])
    return e


def test_copy_moves_every_row_and_verifies_content(tmp_path):
    src = _filled(f"sqlite:///{tmp_path}/a.db")
    dst = make_engine(f"sqlite:///{tmp_path}/b.db")
    Base.metadata.create_all(dst)
    report = copy_database(src, dst)
    v = report["verification"]
    assert v["ok"] and report["copied"]["candidates"] == 50 and v["engine_files"]["n_files"] == 5
    with dst.connect() as c:
        d = c.execute(select(Base.metadata.tables["diseases"])).mappings().one()
    assert d["params"] == {"k": [1, 2]} and d["label"] == "Behçet"
    with pytest.raises(RuntimeError):  # dolu hedefe ikinci kez yazmaz
        copy_database(src, dst)


def test_verify_detects_corrupted_file(tmp_path):
    src = _filled(f"sqlite:///{tmp_path}/a.db")
    dst = make_engine(f"sqlite:///{tmp_path}/b.db")
    Base.metadata.create_all(dst)
    copy_database(src, dst)
    ef = Base.metadata.tables["engine_files"]
    with dst.begin() as c:
        c.execute(update(ef).where(ef.c.path == "data/behcet/f0").values(content=b"bozuk"))
    v = verify(src, dst)
    assert not v["ok"] and v["engine_files"]["sha256_mismatch"] == ["data/behcet/f0"]
