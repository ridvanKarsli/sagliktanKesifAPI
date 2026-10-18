"""Derlem: indirilen makale kayıtlarından analiz girdisini kurar (hastalık makaleleri, köprü gen literatürü, ilaç eşlemesi)."""

from __future__ import annotations

import json
import logging
from collections import Counter

from kesif_engine.catalog import diseases, mesh
from kesif_engine.core import config
from kesif_engine.core.net import HttpClient
from kesif_engine.core.paths import workdir
from kesif_engine.core.store import JsonlCache
from kesif_engine.evaluation import labels
from kesif_engine.extraction import drugmap
from kesif_engine.pipeline.settings import load_settings
from kesif_engine.scoring.ranking import Rec, select_bridges
from kesif_engine.sources import chembl, genes, pubmed, pubtator

log = logging.getLogger("kesif")

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
        from kesif_engine.extraction.textmatch import DrugTextIndex, enrich

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
