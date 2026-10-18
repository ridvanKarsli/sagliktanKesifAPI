"""NCBI Gene kimliklerini resmi sembol ve tam ada çevirir (esummary db=gene). Sonuç önbelleğe alınır."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from kesif_engine.core import config
from kesif_engine.core.net import HttpClient
from kesif_engine.sources.pubmed import EUTILS, _params

log = logging.getLogger(__name__)


def _cache_path() -> Path:
    path = config.DATA_DIR / "genes" / "gene_info.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_cache() -> dict:
    path = _cache_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def parse_summary(result: dict) -> dict:
    out: dict = {}
    for uid in result.get("uids", []):
        item = result.get(uid, {})
        if not isinstance(item, dict) or "error" in item:
            continue
        org = item.get("organism") or {}
        out[uid] = {
            "symbol": item.get("name", ""),
            "description": item.get("description", ""),
            "taxid": org.get("taxid"),
        }
    return out


def fetch_gene_info(client: HttpClient, gene_ids) -> dict:
    """Verilen gen kimlikleri için {gid: {symbol, description, taxid}} döndürür; eksikleri NCBI'dan alır."""
    cache = load_cache()
    need = [g for g in dict.fromkeys(gene_ids) if g not in cache and g.isdigit()]
    for i in range(0, len(need), 200):
        batch = need[i : i + 200]
        resp = client.request(
            "POST", f"{EUTILS}/esummary.fcgi", data=_params({"db": "gene", "id": ",".join(batch), "retmode": "json"})
        )
        got = parse_summary(resp.json().get("result", {}))
        for gid in batch:
            cache[gid] = got.get(gid, {"symbol": "", "description": "", "taxid": None})
    if need:
        _cache_path().write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("%d gen için sembol bilgisi alındı", len(need))
    return {g: cache.get(g, {"symbol": "", "description": "", "taxid": None}) for g in gene_ids}


# ---------- arka plan sayıları (köprü gen özgüllüğü) ----------
#
# Bir genin hastalık literatüründeki payı, tüm literatürdeki payına bölünür (özgüllük). TNF-alpha romatoid
# artrit makalelerinin %6'sında geçer, tüm literatürün %0.8'inde: özgüllük ~8. Hipertansiyon makalelerinin
# yalnızca %0.3'ünde geçer: özgüllük ~0.3 -> köprü olamaz. Böylece her hastalığa aynı sitokin-antikor listesinin
# sızması (hipertansiyonda infliximab, RP'de insülin) engellenir. Sayılar PubTator'dan (etiketlemeyle tutarlı),
# olmazsa PubMed'den (ad araması) alınır ve gen bazında küresel olarak önbelleğe yazılır.

PUBTATOR_SEARCH = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/search/"
TOTAL_QUERY = "all[sb]"
TOTAL_FALLBACK = 38_000_000  # PubMed toplam kayıt (2026); ağ yoksa kullanılır


def _counts_path() -> Path:
    path = config.DATA_DIR / "genes" / "gene_counts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_counts() -> dict:
    path = _counts_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def parse_count(data) -> int | None:
    """PubTator arama yanıtından toplam sayı ('count' / 'total' / 'total_count'); yoksa None."""
    if not isinstance(data, dict):
        return None
    for key in ("count", "total", "total_count"):
        val = data.get(key)
        if isinstance(val, bool):
            continue
        if isinstance(val, int):
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
    return None


def _pubtator_count(client: HttpClient, gid: str):
    try:
        data = client.request("GET", PUBTATOR_SEARCH, params={"text": f"@GENE_{gid}", "page_size": 1}).json()
    except Exception as exc:  # noqa: BLE001 — ağ/format hatası: PubMed'e düş
        log.debug("PubTator sayısı alınamadı (%s): %s", gid, exc)
        return None
    return parse_count(data)


def _pubmed_count(client: HttpClient, names: list):
    from kesif_engine.sources import pubmed

    terms = " OR ".join(f'"{n}"[Title/Abstract]' for n in names if n)
    if not terms:
        return None
    try:
        return pubmed.count(client, f"({terms})")
    except Exception as exc:  # noqa: BLE001
        log.debug("PubMed sayısı alınamadı (%s): %s", names, exc)
        return None


def fetch_gene_counts(client: HttpClient, gene_ids, labels: Optional[dict] = None, info: Optional[dict] = None) -> dict:
    """{gid: {"n": int, "source": "pubtator"|"pubmed"}, "_total": {"n": int}} — eksikleri alır, önbelleğe yazar.

    labels: gid -> PubTator etiketi (PubMed araması için); info: gid -> {symbol, description}.
    Ağ yoksa eldeki önbellek döner; sayısı olmayan gen özgüllük eşiğine takılmaz (bilinmiyor sayılır).
    """
    cache = load_counts()
    labels = labels or {}
    info = info or {}
    changed = False
    need = [g for g in dict.fromkeys(gene_ids) if g not in cache and str(g).isdigit()]
    if not need and "_total" in cache:
        return cache
    # tek sonda: ağ yoksa 70 gen için ayrı ayrı zaman aşımı beklemeyelim
    try:
        from kesif_engine.sources import pubmed

        total = pubmed.count(client, TOTAL_QUERY)
    except Exception as exc:  # noqa: BLE001
        log.warning("Ağ erişimi yok ya da PubMed yanıt vermiyor (%s); gen arka plan sayıları alınamadı.", exc)
        cache.setdefault("_total", {"n": TOTAL_FALLBACK, "source": "fallback"})
        return cache
    if "_total" not in cache or cache["_total"].get("source") == "fallback":
        cache["_total"] = {"n": total, "source": "pubmed"}
        changed = True
    pubtator_ok = True
    for i, gid in enumerate(need, 1):
        n = _pubtator_count(client, gid) if pubtator_ok else None
        source = "pubtator"
        if n is None:
            pubtator_ok = pubtator_ok and i > 3  # ilk denemelerde hiç yanıt yoksa PubTator'ı bırak
            n = _pubmed_count(client, search_names(gid, labels.get(gid, ""), info))
            source = "pubmed"
        if n is None:
            continue
        cache[gid] = {"n": int(n), "source": source}
        changed = True
        if i % 20 == 0:
            log.info("  gen arka plan sayısı: %d/%d", i, len(need))
    if changed:
        _counts_path().write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        if need:
            log.info("%d gen için arka plan sayısı alındı", len(need))
    return cache


def search_names(gid: str, label: str, info: dict) -> list:
    """PubMed aramasında kullanılacak adlar: PubTator etiketi + resmi sembol + tam ad."""
    names = [label]
    rec = info.get(gid) or {}
    for name in (rec.get("symbol"), rec.get("description")):
        if name and name.lower() not in {n.lower() for n in names}:
            names.append(name)
    return names
