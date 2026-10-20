"""Hastalık–gen ilişkisinin literatürden bağımsız kanıtı (Open Targets Platform, GraphQL).

Neden: insan genetiği desteği olan hedeflerde klinik başarı belirgin biçimde yüksektir (Minikel ve ark., Nature 2024:
~2,6 kat). Literatür kanalı "çok yazılan" geni öne çıkarır; genetik kanıt ise yazılma sıklığından bağımsızdır.

Kullanılan veri türleri ve bilinçli dışlamalar:
    genetic_association, somatic_mutation          → genetik kanal
    animal_model, rna_expression, affected_pathway → biyoloji kanalı
    known_drug   KULLANILMAZ: hastalıkta zaten denenmiş ilaçların hedeflerini kodlar (doğrulama etiketini önceden görmek)
    literature   KULLANILMAZ: literatür kanalımızla aynı bilgiyi ikinci kez saymak olur

Hastalık eşleme: katalogdaki MeSH kimliği (MESH:D001528) Open Targets'ta EFO/MONDO kimliğine çapraz başvuru ile
bulunur; bulunamazsa yalnızca adı yeterince benzeyen sonuç kabul edilir, yoksa genetik kanal boş kalır.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Optional

from kesif_engine.core.net import HttpClient

log = logging.getLogger("kesif")

URL = "https://api.platform.opentargets.org/api/v4/graphql"
USED_TYPES = ("genetic_association", "somatic_mutation", "animal_model", "rna_expression", "affected_pathway")
EXCLUDED_TYPES = ("known_drug", "literature")
PAGE_SIZE = 500
MAX_TARGETS = 3000

SEARCH_QUERY = """
query Search($q: String!) {
  search(queryString: $q, entityNames: ["disease"], page: {index: 0, size: 10}) {
    hits { id name }
  }
}
"""

XREF_QUERY = """
query Xrefs($id: String!) {
  disease(efoId: $id) { id name dbXRefs }
}
"""

ASSOC_QUERY = """
query Assoc($id: String!, $index: Int!, $size: Int!) {
  disease(efoId: $id) {
    id
    name
    associatedTargets(page: {index: $index, size: $size}) {
      count
      rows {
        target { id approvedSymbol }
        score
        datatypeScores { id score }
      }
    }
  }
}
"""


def _post(client: HttpClient, query: str, variables: dict) -> dict:
    resp = client.request("POST", URL, json={"query": query, "variables": variables})
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError("; ".join(str(e.get("message")) for e in body["errors"])[:300])
    return body.get("data") or {}


def _mesh_xref(mesh_id: str) -> str:
    """'MESH:D001528' / 'D001528' → 'MESH:D001528' (Open Targets dbXRefs biçimi)."""
    m = (mesh_id or "").strip()
    if not m:
        return ""
    code = m.split(":", 1)[1] if ":" in m else m
    return f"MESH:{code}"


def name_variants(name: str) -> list:
    """MeSH'in ters çevrilmiş adlarını doğal sıraya da çevirir: 'Arthritis, Rheumatoid' → 'Rheumatoid Arthritis',
    'Diabetes Mellitus, Type 2' → 'Type 2 Diabetes Mellitus'. Arama motoru doğal sırada çok daha isabetli."""
    name = (name or "").strip()
    out = []
    if "," in name:
        head, tail = name.split(",", 1)
        out.append(f"{tail.strip()} {head.strip()}")
    out.append(name)
    return [v for i, v in enumerate(out) if v and v not in out[:i]]


def _tokens(text: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t and t not in ("disease", "syndrome", "of", "the")}


def _similar(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def find_disease(client: HttpClient, name: str, mesh_id: str = "") -> Optional[dict]:
    """Hastalığı bulur. Önce MeSH çapraz başvurusu tutan sonuç aranır (her ad biçimiyle); bulunamazsa yalnızca adı
    yeterince benzeyen sonuç kabul edilir. Hiçbiri tutmazsa None: yanlış hastalığın genetiğini kullanmaktansa
    genetik kanal boş kalır (romatoid artrit için "rheumatic fever nodule" eşleşmesi böyle önlenir).
    Dönen: {"id", "name", "matched_by"} ya da None."""
    want = _mesh_xref(mesh_id).upper()
    candidates: list = []
    for q in name_variants(name):
        hits = (_post(client, SEARCH_QUERY, {"q": q}).get("search") or {}).get("hits") or []
        for h in hits:
            if h.get("id") and all(h["id"] != c["id"] for c in candidates):
                candidates.append(h)
        if want:
            for h in hits:
                d = _post(client, XREF_QUERY, {"id": h["id"]}).get("disease") or {}
                if want in {str(x).upper() for x in d.get("dbXRefs") or []}:
                    return {"id": h["id"], "name": d.get("name") or h.get("name"), "matched_by": "mesh"}
    best = max(candidates, key=lambda h: max(_similar(h.get("name", ""), v) for v in name_variants(name)), default=None)
    if best and max(_similar(best.get("name", ""), v) for v in name_variants(name)) >= 0.6:
        return {"id": best["id"], "name": best.get("name"), "matched_by": "ad"}
    return None


def parse_rows(rows) -> Dict[str, Dict[str, float]]:
    """GraphQL satırları → {SEMBOL: {veri_türü: skor}}; yalnızca USED_TYPES tutulur, sıfırlar atılır."""
    out: Dict[str, Dict[str, float]] = {}
    for row in rows or []:
        sym = ((row.get("target") or {}).get("approvedSymbol") or "").upper()
        if not sym:
            continue
        scores = {}
        for dt in row.get("datatypeScores") or []:
            key, val = dt.get("id"), dt.get("score")
            if key in USED_TYPES and val:
                scores[key] = round(float(val), 4)
        if scores:
            cur = out.setdefault(sym, {})
            for k, v in scores.items():
                cur[k] = max(cur.get(k, 0.0), v)
    return out


def associations(client: HttpClient, efo_id: str, max_targets: int = MAX_TARGETS) -> Dict[str, Dict[str, float]]:
    """Hastalıkla ilişkili hedefler ve veri türü skorları (literatür ve bilinen ilaç hariç)."""
    out: Dict[str, Dict[str, float]] = {}
    index, total = 0, None
    while True:
        data = _post(client, ASSOC_QUERY, {"id": efo_id, "index": index, "size": PAGE_SIZE}).get("disease") or {}
        block = data.get("associatedTargets") or {}
        total = block.get("count", 0) if total is None else total
        rows = block.get("rows") or []
        for sym, sc in parse_rows(rows).items():
            out.setdefault(sym, {}).update(sc)
        index += 1
        if not rows or index * PAGE_SIZE >= min(total or 0, max_targets):
            break
    return out


def fetch(client: HttpClient, name_en: str, mesh_id: str = "") -> dict:
    """Bir hastalık için genetik/biyolojik kanıt paketi."""
    disease = find_disease(client, name_en, mesh_id)
    if not disease:
        return {"disease": None, "targets": {}, "note": "Open Targets'ta hastalık bulunamadı."}
    targets = associations(client, disease["id"])
    n_gen = sum(1 for v in targets.values() if any(k in v for k in ("genetic_association", "somatic_mutation")))
    log.info("  genetik kanıt: %s (%s) — %d hedef, %d'inde genetik ilişki", disease["name"], disease["id"], len(targets), n_gen)
    return {"disease": disease, "targets": targets, "used_types": list(USED_TYPES), "excluded_types": list(EXCLUDED_TYPES),
            "n_genetic": n_gen}
