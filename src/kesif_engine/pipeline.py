"""Hattın adımları; komut satırı ve web uygulaması bu modülü ortak kullanır.

Her hastalık için:
  data/<hastalık>/   : corpus_index.json, meta.jsonl, ann.jsonl, labels.json, drug_map.json, settings.json
  results/<hastalık>/: candidates.csv, evidence.json, candidates_novelty.csv, backtest.json, report.html, status.json
"""

from __future__ import annotations

import datetime
import json
import logging
import math
from collections import Counter
from pathlib import Path
from typing import Optional

import pandas as pd

from . import chembl, config, discovery, diseases, drugmap, genes, labels, mesh, novelty, pubmed, pubtator
from .backtest import backtest
from .net import HttpClient
from .ranking import Params, Rec, filter_specific, known_therapies, rank_candidates, select_bridges
from .store import JsonlCache

log = logging.getLogger("kesif")


def workdir(disease: str) -> Path:
    path = config.DATA_DIR / disease
    path.mkdir(parents=True, exist_ok=True)
    return path


def resultsdir(disease: str) -> Path:
    path = config.RESULTS_DIR / disease
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------- ayarlar ----------

def load_settings(disease: str) -> Params:
    path = workdir(disease) / "settings.json"
    if path.exists():
        return Params.from_dict(json.loads(path.read_text(encoding="utf-8")))
    return Params()


def save_settings(disease: str, params: Params) -> None:
    (workdir(disease) / "settings.json").write_text(json.dumps(params.to_dict(), indent=1), encoding="utf-8")


def _mark(disease: str, step: str, **extra) -> None:
    path = resultsdir(disease) / "status.json"
    status = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    status[step] = {"at": datetime.datetime.now().isoformat(timespec="seconds"), **extra}
    path.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")


def status(disease: str) -> dict:
    wd, rd = workdir(disease), resultsdir(disease)
    st = json.loads((rd / "status.json").read_text(encoding="utf-8")) if (rd / "status.json").exists() else {}
    index = json.loads((wd / "corpus_index.json").read_text(encoding="utf-8")) if (wd / "corpus_index.json").exists() else {}

    def _mtime(path: Path):
        return datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")

    # Adım durumu dosyaların kendisinden türetilir; status.json yalnızca ek bilgi taşır.
    artifacts = {
        "fetch": wd / "corpus_index.json",
        "drugs": chembl.dictionary_path(),
        "labels": wd / "labels.json",
        "rank": rd / "candidates.csv",
        "backtest": rd / "backtest.json",
        "novelty": rd / "candidates_novelty.csv",
        "report": rd / "report.html",
        "tune": rd / "tuning.json",
    }
    for step, path in artifacts.items():
        if path.exists():
            st.setdefault(step, {})
            st[step].setdefault("at", index.get("fetched_at") if step == "fetch" and index.get("fetched_at") else _mtime(path))
        else:
            st.pop(step, None)
    if index:
        st["fetch"].setdefault("n_articles", len(index.get("a_pmids", [])))
        st["fetch"].setdefault("n_bridges", len(index.get("c_pmids_by_gene", {})))
    return {
        "disease": disease,
        "has_corpus": bool(index),
        "n_articles": len(index.get("a_pmids", [])),
        "n_bridges": len(index.get("c_pmids_by_gene", {})),
        "fetched_at": index.get("fetched_at"),
        "has_drugs": chembl.dictionary_path().exists(),
        "has_labels": (wd / "labels.json").exists(),
        "has_candidates": (rd / "candidates.csv").exists(),
        "has_novelty": (rd / "candidates_novelty.csv").exists(),
        "has_backtest": (rd / "backtest.json").exists(),
        "has_report": (rd / "report.html").exists(),
        "has_discovery": (rd / "discovery.json").exists(),
        "last_job": json.loads((rd / "last_job.json").read_text(encoding="utf-8")) if (rd / "last_job.json").exists() else None,
        "steps": st,
        "settings": load_settings(disease).to_dict(),
    }


# ---------- veri ----------

def _build_recs(pmids, meta: JsonlCache, ann: JsonlCache) -> list:
    out = []
    for pmid in pmids:
        a = ann.get(pmid)
        if a is None:
            continue
        info = meta.get(pmid) or {}
        out.append(Rec(pmid, info.get("year"), a.get("genes", {}), dict(a.get("chemicals", {})), a.get("relations", []),
                       a.get("diseases", {}), a.get("text", "")))
    return out


def _needs_annotation(ann: JsonlCache, pmid: str) -> bool:
    rec = ann.get(pmid)
    return rec is None or rec.get("schema", 1) < pubtator.SCHEMA


def _ensure(client: HttpClient, meta: JsonlCache, ann: JsonlCache, pmids: list) -> None:
    need_meta = [p for p in pmids if p not in meta]
    for i in range(0, len(need_meta), 1000):
        chunk = need_meta[i : i + 1000]
        got = pubmed.fetch_meta(client, chunk)
        meta.add_many([got.get(p, {"pmid": p, "year": None, "title": ""}) for p in chunk])
        log.info("  künye: %d/%d", min(i + 1000, len(need_meta)), len(need_meta))
    need_ann = [p for p in pmids if _needs_annotation(ann, p)]
    for i in range(0, len(need_ann), 500):
        chunk = need_ann[i : i + 500]
        got = pubtator.annotate(client, chunk)
        ann.add_many([got[p] for p in chunk])
        log.info("  işaretleme: %d/%d", min(i + 500, len(need_ann)), len(need_ann))


def merge_orthologs(a_records, c_by_gene: dict, gene_info: dict) -> dict:
    """Fare/sıçan genlerini aynı sembollü insan genine birleştirir (rd1/Pde6b -> PDE6B, Rpe65 -> RPE65).

    PubTator model organizma genlerini ayrı kimlikle etiketler; RP'de 'rd1' (fare Pde6b) ayrı bir köprü gibi
    görünüyordu. Sembol eşleşmesi (büyük/küçük harf duyarsız) ortolog için yeterli bir yaklaşıklıktır. Kayıtlar
    yerinde güncellenir; gen literatürü listeleri birleştirilir. Dönen: {model_gid: insan_gid}.
    """
    human = {}
    for gid, info in gene_info.items():
        if (info or {}).get("taxid") == 9606 and info.get("symbol"):
            human.setdefault(info["symbol"].upper(), gid)
    mapping = {}
    for gid, info in gene_info.items():
        tax = (info or {}).get("taxid")
        sym = (info or {}).get("symbol") or ""
        if tax and tax != 9606 and sym and sym.upper() in human and human[sym.upper()] != gid:
            mapping[gid] = human[sym.upper()]
    if not mapping:
        return {}

    def fix(rec):
        if any(g in mapping for g in rec.genes):
            new = {}
            for g, lab in rec.genes.items():
                h = mapping.get(g, g)
                new.setdefault(h, lab)
            rec.genes = new
        if rec.relations:
            rec.relations = [[t, t1, mapping.get(i1, i1), t2, mapping.get(i2, i2)] for t, t1, i1, t2, i2 in rec.relations]

    for rec in a_records:
        fix(rec)
    for recs in c_by_gene.values():
        for rec in recs:
            fix(rec)
    for m, h in mapping.items():
        if m in c_by_gene:
            merged = {r.pmid: r for r in c_by_gene.get(h, [])}
            for r in c_by_gene[m]:
                merged.setdefault(r.pmid, r)
            c_by_gene[h] = list(merged.values())
            del c_by_gene[m]
    return mapping


class Corpus:
    def __init__(self, disease: str):
        self.disease = disease
        self.cfg = diseases.get(disease)
        wd = workdir(disease)
        index_path = wd / "corpus_index.json"
        if not index_path.exists():
            raise FileNotFoundError("Önce 'fetch' adımını çalıştırın.")
        self.index = json.loads(index_path.read_text(encoding="utf-8"))
        meta = JsonlCache(wd / "meta.jsonl")
        ann = JsonlCache(wd / "ann.jsonl")
        self.a = _build_recs(self.index["a_pmids"], meta, ann)
        self.c = {g: _build_recs(p, meta, ann) for g, p in self.index["c_pmids_by_gene"].items()}
        self.gene_info = genes.load_cache()
        self.ortholog_map = merge_orthologs(self.a, self.c, self.gene_info)
        if self.ortholog_map:
            log.info("Model organizma genleri insan karşılığına birleştirildi: %s",
                     ", ".join(f"{self.gene_info.get(m, {}).get('symbol') or m}->{self.gene_info.get(h, {}).get('symbol') or h}"
                               for m, h in list(self.ortholog_map.items())[:8]))
        self.gene_counts = genes.load_counts()
        self.disease_id = self._detect_disease_id()
        self.hereditary = mesh.is_hereditary(self.disease_id) or mesh.is_hereditary(self.cfg.get("mesh_id", ""))
        self.labels = labels.load(wd / "labels.json")
        if not self.labels:
            log.warning("Tedavi odaklı etiketler yok ('labels' adımı); backtest yalnızca 'mentions' şemasıyla çalışır.")
        if not any(r.relations for r in self.a):
            log.warning("İlişki verisi yok; yön çıkarımı 'bilinmiyor' kalır. 'fetch' eski kayıtları yeniden işaretler.")
        drugs = chembl.load()
        if not drugs:
            log.warning("ChEMBL ilaç sözlüğü yok ('drugs' adımı). Tüm kimyasallar aday sayılacak.")
            self.drugs, self.drug_map = {}, {}
            return
        self.drugs = {d["chembl_id"]: d for d in drugs if d["max_phase"] >= config.MIN_PHASE}
        if not chembl.has_classification(drugs):
            log.warning("İlaç sözlüğünde ATC bilgisi yok; tanı ajanı/takviye ayıklaması yalnızca ad listeleriyle yapılıyor. "
                        "'İlaç sözlüğü' adımını yeniden çalıştırın (bir kez, ~5 dk).")
        all_recs = self.a + [r for recs in self.c.values() for r in recs]
        # PubTator'ın etiketlemediği biyolojik ilaçları (etanercept, anakinra, interferon alfa...) metinden tara
        from .textmatch import DrugTextIndex, enrich

        n_text = sum(1 for r in self.a if r.text)
        if n_text:
            added = enrich(all_recs, DrugTextIndex(drugs))
            log.info("Metin taramasıyla eklenen biyolojik ilaç anımı: %d", added)
        else:
            log.warning("Makale metni yok; biyolojik ilaçlar (etanercept, anakinra...) eksik kalabilir. 'fetch' kayıtları yeniler.")
        votes: dict = {}
        for rec in all_recs:
            for cid, lab in rec.chemicals.items():
                votes.setdefault(cid, Counter())[lab] += 1
        self.drug_map = drugmap.build_map(votes, drugs, drugmap.load_manual(wd))
        for cid in votes:
            if cid.startswith("CHEMBL:"):
                self.drug_map[cid] = cid.split(":", 1)[1]
        (wd / "drug_map.json").write_text(json.dumps(self.drug_map, ensure_ascii=False, indent=1), encoding="utf-8")

    def _detect_disease_id(self) -> str:
        """Hastalığın PubTator kimliğini makalelerden bulur: hastalık literatüründe en sık etiketlenen hastalık.

        Elle girilen MeSH kimliği yanlış olabilir (FMF: yapılandırmada D005258, PubTator'da D010505);
        makalelerin kendisi daha güvenilir. Yapılandırmadaki kimlik ilk üçteyse o korunur.
        """
        counts: Counter = Counter()
        for rec in self.a:
            for did in rec.diseases:
                counts[did] += 1
        if not counts:
            return self.cfg.get("mesh_id") or ""
        top = [d for d, _ in counts.most_common(3)]
        configured = self.cfg.get("mesh_id")
        if configured in top:
            return configured
        detected = top[0]
        if configured and configured != detected:
            log.warning("Hastalık kimliği düzeltildi: yapılandırma %s, makalelerde %s (%d makale)", configured, detected, counts[detected])
        return detected

    def ensure_gene_counts(self, client: HttpClient = None) -> dict:
        """Köprü genlerin arka plan sayılarını (özgüllük için) tamamlar; ağ yoksa eldekiyle devam eder."""
        p = load_settings(self.disease)
        if p.min_spec <= 0:
            return self.gene_counts
        bridges = select_bridges(self.a, p)
        need = [g for g, _, _, _ in bridges if g not in self.gene_counts]
        if need:
            try:
                self.gene_counts = genes.fetch_gene_counts(client or HttpClient(timeout=20, retries=1), [g for g, _, _, _ in bridges],
                                                           {g: lab for g, lab, _, _ in bridges}, self.gene_info)
            except Exception as exc:  # noqa: BLE001
                log.warning("Gen arka plan sayıları alınamadı (%s); özgüllük eşiği eksik genlere uygulanmayacak.", exc)
            still = [g for g in need if g not in self.gene_counts]
            if still:
                log.warning("%d köprü genin arka plan sayısı yok; bunlara özgüllük eşiği uygulanmadı.", len(still))
        return self.gene_counts

    @property
    def therapy_pmids(self):
        return (self.labels or {}).get("therapy_pmids") or []

    def kwargs(self) -> dict:
        return {"drugs": self.drugs, "drug_map": self.drug_map, "gene_info": self.gene_info, "disease_id": self.disease_id,
                "gene_counts": self.gene_counts, "hereditary": self.hereditary}


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
    from .report import build_report

    step_discovery(disease)
    path = build_report(resultsdir(disease), diseases.get(disease), top)
    _mark(disease, "report")
    return path


def step_tune(disease: str, cutoff_a: int = 2016, cutoff_b: int = 2019, apply: bool = True, corpus: Corpus = None) -> dict:
    from .tuning import tune

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


# ---------- okuma yardımcıları (web) ----------

def load_candidates(disease: str) -> list:
    out = resultsdir(disease)
    path = out / "candidates.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path, index_col="rank")
    nov = out / "candidates_novelty.csv"
    if nov.exists():
        n = pd.read_csv(nov)[["chemical_id", "clinical_trials", "pubmed_direct", "durum"]]
        df = df.reset_index().merge(n, on="chemical_id", how="left").set_index("rank")
    rows = []
    for rank, r in df.iterrows():
        d = {}
        for k, v in r.to_dict().items():
            if isinstance(v, float) and math.isnan(v):
                v = None
            elif hasattr(v, "item"):
                v = v.item()
            d[k] = v
        d["rank"] = int(rank)
        for k in ("clinical_trials", "pubmed_direct"):
            if d.get(k) is not None:
                d[k] = int(d[k])
        for k in ("aliases", "mechanism", "direct_pmids", "top_bridges", "chembl_id", "durum"):
            if d.get(k) is None:
                d[k] = ""
        # eski sıralama dosyalarıyla uyum: sınıf/durum/ceza sütunları yoksa varsayılanlar
        d.setdefault("kind", "ilaç")
        d.setdefault("status", "tedavi olarak çalışılmış" if (d.get("n_treat_rel") or 0) >= 2 else ("literatürde anılmış" if d.get("n_direct_a") else "yeni"))
        d.setdefault("penalty", 1.0)
        if d.get("kind") is None:
            d["kind"] = "ilaç"
        if d.get("penalty") is None:
            d["penalty"] = 1.0
        # Yenilik kontrolü (PubMed tam sayım + ClinicalTrials.gov) örneklenmiş derlemeden daha güvenilirdir:
        # örneklemde hiç anılmamış görünen ilaç PubMed'de hastalıkla 395 kez geçebilir (saralasin-hipertansiyon).
        if d.get("clinical_trials"):
            d["status"] = "tedavi olarak çalışılmış"
        elif d.get("pubmed_direct") and d.get("status") == "yeni":
            d["status"] = "literatürde anılmış"
        rows.append(d)
    return rows


def load_known(disease: str) -> list:
    path = resultsdir(disease) / "known_therapies.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path, index_col="rank")
    rows = []
    for rank, r in df.iterrows():
        d = {k: (None if isinstance(v, float) and math.isnan(v) else (v.item() if hasattr(v, "item") else v)) for k, v in r.to_dict().items()}
        d["rank"] = int(rank)
        rows.append(d)
    return rows


def load_evidence(disease: str, chemical_id: str) -> list:
    path = resultsdir(disease) / "evidence.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get(chemical_id, [])


def load_backtest(disease: str) -> dict:
    path = resultsdir(disease) / "backtest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
