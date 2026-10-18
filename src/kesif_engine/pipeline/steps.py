"""Analiz adımları ve sıralı çalıştırıcı: fetch → drugs → labels → tune → rank → backtest → novelty → report."""

from __future__ import annotations

import datetime
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Optional

import pandas as pd

from kesif_engine.catalog import diseases
from kesif_engine.core import config
from kesif_engine.core.net import HttpClient
from kesif_engine.core.paths import resultsdir, workdir
from kesif_engine.core.store import JsonlCache
from kesif_engine.evaluation import labels
from kesif_engine.evaluation.backtest import backtest
from kesif_engine.insights import discovery, novelty
from kesif_engine.pipeline.corpus import Corpus, _build_recs, _ensure
from kesif_engine.pipeline.settings import load_settings, save_settings
from kesif_engine.pipeline.status import _mark
from kesif_engine.scoring.ranking import Params, filter_specific, known_therapies, rank_candidates, select_bridges
from kesif_engine.sources import chembl, genes, pubmed

log = logging.getLogger("kesif")

# ---------- adımlar ----------

MAX_ARTICLES_DEFAULT = 25000  # daha büyük hastalıklarda yıllara orantılı örnekleme yapılır
SMALL_CORPUS = 500             # bunun altında sonuçlar zayıf olur; uyarı verilir


def estimate(disease: str, start_year: int = 1990, max_a: int = MAX_ARTICLES_DEFAULT) -> dict:
    """Başlamadan önce: PubMed makale sayısı, indirilecek sayı ve kaba süre tahmini."""
    cfg = diseases.get(disease)
    total = pubmed.count(HttpClient(), cfg["pubmed_query"])
    n_download = min(total, max_a) if max_a else total
    # ölçülen hızlar: künye ~110/sn, işaretleme ~45/sn, gen literatürü ~70 gen x 300 makale
    seconds = n_download / 110 + n_download / 45 + 70 * 300 / 45 + 120
    return {"pubmed_total": total, "will_download": n_download, "sampled": bool(max_a) and total > max_a,
            "estimated_minutes": int(round(seconds / 60)), "small": total < SMALL_CORPUS}


PER_GENE_RECENT = 150  # her gen için ayrıca en yeni makaleler: yeni ajanlar (baxdrostat, retatrutide) ancak böyle görülür


def step_fetch(disease: str, params: Params = None, start_year: int = 1990, max_a: int = MAX_ARTICLES_DEFAULT, per_gene: int = 300,
               per_gene_recent: int = PER_GENE_RECENT) -> dict:
    cfg = diseases.get(disease)
    p = params or load_settings(disease)
    wd = workdir(disease)
    client = HttpClient()
    meta = JsonlCache(wd / "meta.jsonl")
    ann = JsonlCache(wd / "ann.jsonl")

    log.info("[1/3] %s makaleleri aranıyor...", cfg["label"])
    this_year = datetime.date.today().year
    a_pmids = pubmed.search_by_year(client, cfg["pubmed_query"], start_year, this_year, max_a)
    log.info("  %d makale bulundu", len(a_pmids))
    if len(a_pmids) < SMALL_CORPUS:
        log.warning("  Küçük derleme (%d makale): köprü genler ve adaylar az olur; sonuçlar temkinli yorumlanmalı.", len(a_pmids))
    _ensure(client, meta, ann, a_pmids)

    log.info("[2/3] Köprü genler seçiliyor ve gen literatürü toplanıyor...")
    a_records = _build_recs(a_pmids, meta, ann)
    bridges = select_bridges(a_records, p)
    n_primary = sum(1 for b in bridges if b[3] == "primary")
    log.info("  %d köprü gen (%d doğrudan + %d yolak komşusu)", len(bridges), n_primary, len(bridges) - n_primary)
    info = genes.fetch_gene_info(client, [g for g, _, _, _ in bridges])
    if p.min_spec > 0:
        counts = genes.fetch_gene_counts(client, [g for g, _, _, _ in bridges], {g: lab for g, lab, _, _ in bridges}, info)
        bridges, dropped = filter_specific(bridges, len(a_records), counts, p.min_spec)
        if dropped:
            log.info("  özgüllüğü düşük %d gen köprü olmadı (hastalığa özgü değil): %s", len(dropped),
                     ", ".join(f"{lab} ({sp})" for _, lab, sp in dropped[:12]))
    # var olan indeksi koru: daha önce indirilmiş genler yeniden aranmaz
    old = json.loads((wd / "corpus_index.json").read_text(encoding="utf-8")) if (wd / "corpus_index.json").exists() else {}
    old_c = old.get("c_pmids_by_gene", {}) if old.get("per_gene") == per_gene else {}
    old_recent = old.get("c_recent_by_gene", {}) if old.get("per_gene_recent") == per_gene_recent else {}
    c_index, c_recent, gene_labels, gene_source = {}, {}, {}, {}
    for gid, label, n, source in bridges:
        names = genes.search_names(gid, label, info)
        name_q = " OR ".join(f'"{x}"[Title/Abstract]' for x in names)
        query = f"({name_q}) AND (inhibitor OR antagonist OR agonist OR drug OR therapy OR treatment)"
        if gid in old_c:
            c_index[gid] = old_c[gid]
        else:
            c_index[gid] = pubmed.search_pmids(client, query, retmax=per_gene, sort="relevance")
            log.info("  %s (%s, %s): %d makale", label, info[gid].get("symbol") or "?", source, len(c_index[gid]))
        if per_gene_recent > 0:
            # en yeni makaleler: ilgililik sıralaması eski klasik çalışmaları öne alır, son 3-5 yılın yeni ajanları
            # (baxdrostat, retatrutide, depemokimab) ancak tarih sıralı ek örnekle görülür
            if gid in old_recent:
                c_recent[gid] = old_recent[gid]
            else:
                c_recent[gid] = pubmed.search_pmids(client, query, retmax=per_gene_recent, sort="pub_date")
            seen = set(c_index[gid])
            c_index[gid] = c_index[gid] + [x for x in c_recent[gid] if x not in seen]
        gene_labels[gid] = label
        gene_source[gid] = source
    all_c = sorted({x for pm in c_index.values() for x in pm})
    _ensure(client, meta, ann, all_c)

    log.info("[3/3] İndeks yazılıyor...")
    index = {
        "disease": disease,
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "per_gene": per_gene,
        "per_gene_recent": per_gene_recent,
        "max_a": max_a,
        "params": p.to_dict(),
        "a_pmids": a_pmids,
        "c_pmids_by_gene": c_index,
        "c_recent_by_gene": c_recent,
        "gene_labels": gene_labels,
        "gene_source": gene_source,
    }
    (wd / "corpus_index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    _mark(disease, "fetch", n_articles=len(a_pmids), n_bridges=len(bridges))
    return {"n_articles": len(a_pmids), "n_bridges": len(bridges)}


def step_drugs(force: bool = False, min_phase: Optional[float] = None) -> dict:
    if chembl.dictionary_path().exists() and not force:
        existing = chembl.load()
        if chembl.has_classification(existing):
            log.info("İlaç sözlüğü zaten var: %s", chembl.dictionary_path())
            return {"n_drugs": len(existing)}
        log.info("İlaç sözlüğü ATC/doğal ürün bilgisi olmadan kurulmuş; tanı ajanı ve takviye ayıklaması için yenileniyor.")
    drugs = chembl.build(HttpClient(), config.MIN_PHASE if min_phase is None else min_phase)
    return {"n_drugs": len(drugs)}


def step_labels(disease: str, force: bool = False, start_year: int = 1990) -> dict:
    cfg = diseases.get(disease)
    out = workdir(disease) / "labels.json"
    if out.exists() and not force:
        existing = labels.load(out)
        if existing.get("schema", 1) >= labels.SCHEMA:
            log.info("Etiketler zaten var: %s", out)
            return {"n_therapy": len(existing.get("therapy_pmids", []))}
        log.info("Etiketler eski şemada (tüm kollar); deneysel kol ve çalışma türü bilgisiyle yenileniyor.")
    lab = labels.build(HttpClient(), cfg, chembl.load(), config.MIN_PHASE, start_year, out)
    _mark(disease, "labels", n_therapy=len(lab["therapy_pmids"]), n_trials=len(lab["trials"]))
    return {"n_therapy": len(lab["therapy_pmids"]), "n_trials": len(lab["trials"])}


def step_rank(disease: str, params: Params = None, corpus: Corpus = None) -> pd.DataFrame:
    corpus = corpus or Corpus(disease)
    p = params or load_settings(disease)
    corpus.ensure_gene_counts()
    ranked, evidence = rank_candidates(corpus.a, corpus.c, params=p, therapy_pmids=corpus.therapy_pmids, **corpus.kwargs())
    out = resultsdir(disease)
    ranked.to_csv(out / "candidates.csv", encoding="utf-8")
    (out / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "bridges_dropped.json").write_text(json.dumps(ranked.attrs.get("dropped_bridges", []), ensure_ascii=False), encoding="utf-8")
    kinds = Counter(ranked["kind"]) if len(ranked) else Counter()
    log.info("Aday sınıfları: %s", ", ".join(f"{k} {n}" for k, n in kinds.most_common()))
    known = known_therapies(corpus.a, corpus.disease_id, corpus.drugs, corpus.drug_map,
                            therapy_pmids=(corpus.labels or {}).get("therapy_pmids"))
    known.to_csv(out / "known_therapies.csv", encoding="utf-8")
    # eski yenilik sonuçları yeni sıralamayla uyumsuz olabilir; aday kümesi değiştiyse sil
    nov = out / "candidates_novelty.csv"
    if nov.exists():
        old = set(pd.read_csv(nov)["chemical_id"])
        if not old <= set(ranked["chemical_id"]):
            nov.unlink()
    _mark(disease, "rank", n_candidates=len(ranked), params=p.to_dict())
    log.info("%d aday yazıldı: %s", len(ranked), out / "candidates.csv")
    return ranked


def step_backtest(disease: str, cutoff: int = 2018, params: Params = None, corpus: Corpus = None) -> dict:
    corpus = corpus or Corpus(disease)
    p = params or load_settings(disease)
    corpus.ensure_gene_counts()
    result = backtest(corpus.a, corpus.c, cutoff, labels=corpus.labels, params=p, **corpus.kwargs())
    out = resultsdir(disease)
    (out / "backtest.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    hist_path = out / "backtest_history.json"
    hist = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []
    hist.append({"at": datetime.datetime.now().isoformat(timespec="seconds"), "cutoff": cutoff, "params": p.to_dict(),
                 "summary": {s: r.get("metrics") for s, r in result["schemes"].items()}})
    hist_path.write_text(json.dumps(hist[-50:], ensure_ascii=False, indent=1), encoding="utf-8")
    _mark(disease, "backtest", cutoff=cutoff)
    return result


def step_novelty(disease: str, top: int = 100) -> pd.DataFrame:
    cfg = diseases.get(disease)
    out = resultsdir(disease)
    df = pd.read_csv(out / "candidates.csv", index_col="rank")
    result = novelty.add_novelty(HttpClient(), df, cfg, top)
    result.to_csv(out / "candidates_novelty.csv", encoding="utf-8")
    _mark(disease, "novelty", top=top)
    return result


def step_discovery(disease: str) -> dict:
    """Keşif özeti: yeni adaylar, bağımsız doğrulama, hedef sınıfları (results/<hastalık>/discovery.json)."""
    cfg = dict(diseases.get(disease))
    cfg["key"] = disease
    summary = discovery.build_summary(resultsdir(disease), workdir(disease), cfg)
    if summary:
        log.info("Keşif özeti: %d yeni aday, %d'i kontrol edildi, %d'inin klinik çalışması var",
                 summary["n_new"], summary["n_new_checked"], summary["n_new_with_trials"])
    return summary


def step_report(disease: str, top: int = 100) -> Path:
    from kesif_engine.insights.report import build_report

    step_discovery(disease)
    path = build_report(resultsdir(disease), diseases.get(disease), top)
    _mark(disease, "report")
    return path


def step_tune(disease: str, cutoff_a: int = 2016, cutoff_b: int = 2019, apply: bool = True, corpus: Corpus = None) -> dict:
    from kesif_engine.evaluation.tuning import tune

    corpus = corpus or Corpus(disease)
    base = load_settings(disease)
    result = tune(corpus, base, cutoff_a, cutoff_b)
    result["applied"] = False
    if apply and result["validation_on_b"]["validated"]:
        save_settings(disease, Params.from_dict(result["best_params"]))
        result["applied"] = True
    (resultsdir(disease) / "tuning.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    _mark(disease, "tune", applied=result["applied"], cutoff_a=cutoff_a, cutoff_b=cutoff_b)
    log.info("Parametre ayarı: %s", result["verdict"])
    return result


STEP_ORDER = ["fetch", "drugs", "labels", "tune", "rank", "backtest", "novelty", "report"]
DEFAULT_STEPS = ["fetch", "drugs", "labels", "rank", "backtest", "novelty", "report"]


def run_steps(disease: str, steps: list, params: Params = None, cutoff: int = 2018, top: int = 100,
              fetch_opts: Optional[dict] = None, force: bool = False) -> dict:
    """Seçilen adımları sırayla çalıştırır; her adımın özetini döndürür."""
    p = params or load_settings(disease)
    if params is not None:
        save_settings(disease, params)
    summary: dict = {}
    corpus = None
    fetch_only = {k: v for k, v in (fetch_opts or {}).items() if k in ("start_year", "max_a", "per_gene")}
    for step in STEP_ORDER:
        if step not in steps:
            continue
        log.info("=== %s: %s ===", disease, step)
        if step == "fetch":
            summary[step] = step_fetch(disease, p, **fetch_only)
            corpus = None
        elif step == "drugs":
            summary[step] = step_drugs(force=force)
            corpus = None
        elif step == "labels":
            # Etiketler yalnızca doğrulama içindir: dış servis (ClinicalTrials.gov/PubMed) yanıt vermezse iş durmaz,
            # eksik etiketle devam edilir ve özet bunu belirtir.
            try:
                summary[step] = step_labels(disease, force=force)
            except Exception as exc:  # noqa: BLE001
                log.warning("Doğrulama etiketleri alınamadı (%s); mevcut etiketlerle devam ediliyor.", exc)
                summary[step] = {"error": f"{type(exc).__name__}: {exc}", "skipped": True}
            corpus = None
        elif step == "tune":
            corpus = corpus or Corpus(disease)
            res = step_tune(disease, cutoff_a=(fetch_opts or {}).get("cutoff_a", 2016), cutoff_b=(fetch_opts or {}).get("cutoff_b", 2019), corpus=corpus)
            summary[step] = {"applied": res["applied"], "verdict": res["verdict"], "best_params": res["best_params"]}
            if res["applied"]:
                p = load_settings(disease)
        elif step == "rank":
            corpus = corpus or Corpus(disease)
            summary[step] = {"n_candidates": len(step_rank(disease, p, corpus))}
        elif step == "backtest":
            corpus = corpus or Corpus(disease)
            res = step_backtest(disease, cutoff, p, corpus)
            summary[step] = {"primary_scheme": res.get("primary_scheme"), "metrics": res.get("metrics")}
        elif step == "novelty":
            try:
                summary[step] = {"n_checked": len(step_novelty(disease, top))}
            except Exception as exc:  # noqa: BLE001
                log.warning("Yenilik kontrolü tamamlanamadı (%s); rapor yenilik bilgisi olmadan üretilecek.", exc)
                summary[step] = {"error": f"{type(exc).__name__}: {exc}", "skipped": True}
        elif step == "report":
            summary[step] = {"path": str(step_report(disease, top))}
    return summary
