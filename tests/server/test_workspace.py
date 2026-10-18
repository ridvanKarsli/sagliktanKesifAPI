"""Çalışma dizini ↔ veritabanı eşitlemesi (gerçek SQLite, gerçek dosyalar)."""

from __future__ import annotations

from kesif_server.infrastructure.db.models import Base
from kesif_server.infrastructure.db.session import make_engine, make_session_factory
from kesif_server.infrastructure.db.uow import make_uow_factory
from kesif_server.infrastructure.engine.workspace import Workspace


def _uow():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return make_uow_factory(make_session_factory(engine))


def test_roundtrip_and_incremental(tmp_path):
    uow = _uow()
    ws = Workspace(uow, tmp_path / "w1")
    (ws.data_dir / "drugs").mkdir(parents=True)
    (ws.data_dir / "drugs" / "chembl_drugs.json").write_text("{}", encoding="utf-8")
    (ws.data_dir / "behcet").mkdir()
    (ws.data_dir / "behcet" / "articles.jsonl").write_bytes(b"x" * 10000)
    (ws.results_dir / "behcet").mkdir()
    (ws.results_dir / "behcet" / "candidates.csv").write_text("rank\n1\n", encoding="utf-8")
    (ws.results_dir / "behcet" / "job.log").write_text("log", encoding="utf-8")  # günlük yüklenmez

    assert ws.save("behcet") == {"uploaded": 3, "unchanged": 0, "deleted": 0}
    assert ws.save("behcet") == {"uploaded": 0, "unchanged": 3, "deleted": 0}

    # başka bir makine / boş çalışma dizini
    ws2 = Workspace(uow, tmp_path / "w2")
    assert ws2.prepare("behcet") == {"downloaded": 3, "up_to_date": 0, "removed": 0}
    assert (ws2.data_dir / "behcet" / "articles.jsonl").read_bytes() == b"x" * 10000
    assert (ws2.results_dir / "behcet" / "candidates.csv").read_text(encoding="utf-8") == "rank\n1\n"
    assert not (ws2.results_dir / "behcet" / "job.log").exists()
    assert ws2.prepare("behcet") == {"downloaded": 0, "up_to_date": 3, "removed": 0}

    # değişiklik ve silme yayılır
    (ws2.results_dir / "behcet" / "candidates.csv").write_text("rank\n1\n2\n", encoding="utf-8")
    (ws2.data_dir / "behcet" / "articles.jsonl").unlink()
    assert ws2.save("behcet") == {"uploaded": 1, "unchanged": 1, "deleted": 1}
    assert ws.prepare("behcet") == {"downloaded": 1, "up_to_date": 1, "removed": 1}  # silinen dosya yerelden de kalkar
    assert (ws.results_dir / "behcet" / "candidates.csv").read_text(encoding="utf-8").count("\n") == 3
    assert not (ws.data_dir / "behcet" / "articles.jsonl").exists()

    # ortak kapsam hastalık verilmeden de eşitlenir; kalıcı silme kapsamı boşaltır
    assert ws2.prepare() == {"downloaded": 0, "up_to_date": 1, "removed": 0}
    assert ws.discard("behcet") == 1
    assert not (ws.data_dir / "behcet").exists()
    assert ws2.prepare("behcet") == {"downloaded": 0, "up_to_date": 1, "removed": 1}


def test_import_tree(tmp_path):
    uow = _uow()
    legacy = tmp_path / "old"
    (legacy / "data" / "fmf").mkdir(parents=True)
    (legacy / "data" / "fmf" / "a.txt").write_text("a", encoding="utf-8")
    (legacy / "results" / "fmf").mkdir(parents=True)
    (legacy / "results" / "fmf" / "b.txt").write_text("b", encoding="utf-8")
    ws = Workspace(uow, tmp_path / "w")
    ws.import_tree(legacy)
    assert ws.save("fmf")["uploaded"] == 2
