from __future__ import annotations

from kesif_server.infrastructure.engine.progress import ProgressTracker


def test_tracker_translates_engine_log():
    seen = []
    t = ProgressTracker(seen.append)
    t.feed("=== behcet: fetch ===")
    t.feed("[1/3] Behçet makaleleri aranıyor...")
    t.feed("  künye: 1000/4903")
    t.feed("  işaretleme: 4.500/4.903")
    t.feed("=== behcet: rank ===")
    t.finish()
    assert seen[0].step == "fetch" and "Makaleler" in seen[0].message
    assert seen[2].done == 1000 and seen[2].total == 4903
    assert seen[3].done == 4500 and "işleniyor" in seen[3].message
    assert seen[4].step == "rank" and seen[4].completed_steps == ["fetch"]
    assert seen[-1].completed_steps == ["fetch", "rank"] and seen[-1].message == "Tamamlandı"


def test_unknown_lines_ignored():
    seen = []
    t = ProgressTracker(seen.append)
    t.feed("something irrelevant 3/4")
    assert seen == []
