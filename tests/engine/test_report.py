import json

from kesif_engine.evaluation.backtest import backtest
from kesif_engine.insights.report import build_report
from kesif_engine.scoring.ranking import rank_candidates
from tests.engine.demo_data import DISEASE, demo_drugs, make_corpus


def test_report_builds_self_contained_html(tmp_path):
    a, c = make_corpus()
    drugs, drug_map = demo_drugs()
    ranked, evidence = rank_candidates(a, c, drugs=drugs, drug_map=drug_map, disease_id=DISEASE)
    ranked.to_csv(tmp_path / "candidates.csv", encoding="utf-8")
    (tmp_path / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    bt = backtest(a, c, 2015, drugs=drugs, drug_map=drug_map, disease_id=DISEASE)
    (tmp_path / "backtest.json").write_text(json.dumps(bt), encoding="utf-8")
    out = build_report(tmp_path, {"label": "Demo hastalığı"}, top_n=10)
    html = out.read_text(encoding="utf-8")
    assert "<!doctype html>" in html and "futuredrug1" in html
    assert "tedavi önerisi değildir" in html and "Geriye dönük doğrulama" in html
    assert "pubmed.ncbi.nlm.nih.gov" in html and "chembl" in html
