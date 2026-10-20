"""PubTator 3.0: makalelerdeki gen, kimyasal, hastalık işaretlemeleri ve varlıklar arası ilişkiler.

İşaretleme (annotations) biçimi gerçek API ile doğrulandı. İlişkiler (relations) için PubTator 3.0
BioC-JSON çıktısındaki document/passage düzeyi "relations" alanı ayrıştırılır; rol alanları sözlük
({"type","identifier"}) ya da "Tip|kimlik" biçiminde dizge olabilir, ikisi de desteklenir.
İlişki tipleri (PubTator 3.0): Positive_Correlation, Negative_Correlation, Association, Bind,
Cotreatment, Comparison, Drug_Interaction, Conversion.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Dict, Optional

import requests

from kesif_engine.core.net import HttpClient

log = logging.getLogger(__name__)

URL = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson"
BATCH = 100
BUCKETS = {"Gene": "genes", "Chemical": "chemicals", "Disease": "diseases"}
SCHEMA = 3  # ann.jsonl kayıt sürümü; eski sürüm kayıtlar yeniden işaretlenir (3: makale metni eklendi)
MAX_TEXT = 4000  # saklanan başlık+özet metni üst sınırı (karakter)


def empty_record(pmid: str, pending: bool = False) -> dict:
    """Boş kayıt. pending=True: PubTator bu makaleyi henüz işlememiş (çok yeni PMID); şema 0 yazılır ki bir
    sonraki 'fetch' yeniden denesin."""
    return {"pmid": pmid, "schema": 0 if pending else SCHEMA, "genes": {}, "chemicals": {}, "diseases": {}, "relations": [], "text": ""}


def _docs_from_obj(obj) -> list:
    if isinstance(obj, list):
        return [d for d in obj if isinstance(d, dict)]
    if isinstance(obj, dict):
        for key in ("PubTator3", "documents"):
            if isinstance(obj.get(key), list):
                return [d for d in obj[key] if isinstance(d, dict)]
        if "passages" in obj:
            return [obj]
    return []


def _extract_docs(text: str) -> list:
    text = text.strip()
    if not text:
        return []
    try:
        return _docs_from_obj(json.loads(text))
    except json.JSONDecodeError:
        docs: list = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                docs.extend(_docs_from_obj(json.loads(line)))
            except json.JSONDecodeError:
                continue
        return docs


def _split_ids(ident) -> list:
    out = []
    for one in str(ident or "").split(";"):
        one = one.strip()
        if one and one not in ("-", "None"):
            out.append(one)
    return out


def _role(value):
    """İlişki rolünü (tip, kimlik) olarak döndürür."""
    if isinstance(value, dict):
        return value.get("type"), value.get("identifier") or value.get("id")
    if isinstance(value, str):
        if "|" in value:
            t, i = value.split("|", 1)
            return t.strip(), i.strip()
        return None, value.strip()
    return None, None


def _relations(doc: dict) -> list:
    rels = list(doc.get("relations") or [])
    for passage in doc.get("passages", []):
        rels.extend(passage.get("relations") or [])
    out = []
    for rel in rels:
        infons = rel.get("infons", {}) or {}
        rtype = infons.get("type") or rel.get("type")
        t1, i1 = _role(infons.get("role1"))
        t2, i2 = _role(infons.get("role2"))
        if not (rtype and i1 and i2):
            continue
        for a in _split_ids(i1):
            for b in _split_ids(i2):
                out.append([rtype, t1 or "", a, t2 or "", b])
    return out


def _doc_to_record(pmid: str, doc: dict) -> dict:
    mentions: dict = {}
    for passage in doc.get("passages", []):
        for ann in passage.get("annotations", []):
            infons = ann.get("infons", {}) or {}
            bucket = BUCKETS.get(str(infons.get("type") or ""))
            ident = infons.get("identifier") or infons.get("Identifier")
            if not bucket or not ident:
                continue
            text = (ann.get("text") or "").strip()
            for one in _split_ids(ident):
                mentions.setdefault((bucket, one), Counter())[text] += 1
    record = empty_record(pmid)
    for (bucket, ident), counter in mentions.items():
        record[bucket][ident] = counter.most_common(1)[0][0]
    record["relations"] = _relations(doc)
    # Başlık + özet metni: PubTator'ın etiketlemediği biyolojik ilaç adlarını (etanercept, anakinra,
    # interferon-alfa...) sözlükle taramak için saklanır.
    text = " ".join((p.get("text") or "").strip() for p in doc.get("passages", []))
    record["text"] = text[:MAX_TEXT]
    return record


def parse_biocjson(text: str) -> dict:
    out: dict = {}
    for doc in _extract_docs(text):
        pmid = str(doc.get("pmid") or doc.get("id") or "")
        if pmid:
            out[pmid] = _doc_to_record(pmid, doc)
    return out


# PubTator, PubMed'in birkaç gün–birkaç hafta gerisinden gelir: en yeni makaleler henüz işlenmemiştir. Bunları tek tek
# sormak (grup ikiye bölünerek) makale başına ~1 sn sürer; en yeni binlerce makalede saatler kaybettirir. İlk "henüz
# işlenmemiş" makale görüldüğünde onun PMID'si eşik olur: daha yeni PMID'ler sorulmadan "bekliyor" işaretlenir ve bir
# sonraki indirmede yeniden denenir. Eşik yalnızca gerçekten yeni bölgede kurulur (görülen en büyük PMID'nin WINDOW
# kadar altına inmez); böylece tek bir tuhaf eski makale binlerce makaleyi bekletmez.
WINDOW = 300_000  # ≈ 6–8 haftalık PubMed eklemesi
_state: Dict[str, Optional[int]] = {"floor": None, "max_seen": 0}


def reset_pending_floor() -> None:
    _state["floor"], _state["max_seen"] = None, 0


def _pid(pmid) -> int:
    try:
        return int(pmid)
    except (TypeError, ValueError):
        return 0


def _note_pending(pmid: str) -> None:
    n = _pid(pmid)
    floor, max_seen = _state["floor"], _state["max_seen"] or 0
    if n and n >= max_seen - WINDOW and (floor is None or n < floor):
        _state["floor"] = n


def _fetch_batch(client: HttpClient, batch: list, pending: list) -> dict:
    """Bir grubu işaretler. PubTator henüz işlenmemiş PMID içeren gruba 400/404 döndürür; grup ikiye bölünerek sorunlu
    makale yalnız bırakılır ve 'bekliyor' (şema 0) kaydedilir."""
    try:
        resp = client.request("GET", URL, params={"pmids": ",".join(batch)})
        return parse_biocjson(resp.text)
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        if code not in (400, 404):
            raise
        if len(batch) == 1:
            pending.append(batch[0])
            _note_pending(batch[0])
            return {batch[0]: empty_record(batch[0], pending=True)}
        mid = len(batch) // 2
        out = _fetch_batch(client, batch[:mid], pending)
        out.update(_fetch_batch(client, batch[mid:], pending))
        return out


def annotate(client: HttpClient, pmids: list) -> dict:
    out: dict = {}
    pending: list = []
    skipped = 0
    for i in range(0, len(pmids), BATCH):
        batch = pmids[i : i + BATCH]
        _state["max_seen"] = max(_state["max_seen"] or 0, max((_pid(p) for p in batch), default=0))
        floor = _state["floor"]
        ask = [p for p in batch if floor is None or _pid(p) < floor]
        parsed = _fetch_batch(client, ask, pending) if ask else {}
        for pmid in batch:
            if pmid in parsed:
                out[pmid] = parsed[pmid]
            elif floor is not None and _pid(pmid) >= floor:
                out[pmid] = empty_record(pmid, pending=True)
                skipped += 1
            else:
                out[pmid] = empty_record(pmid)
    if pending or skipped:
        log.info("PubTator henüz işlememiş: %d makale (en yeni PMID'ler; %d tanesi sorulmadan atlandı). "
                 "Bir sonraki indirmede yeniden denenecek.", len(pending) + skipped, skipped)
    return out
