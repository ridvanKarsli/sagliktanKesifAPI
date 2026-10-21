"""İnsan protein etkileşim ağı (STRING). Ağ yakınlığı kanalının girdisi.

Yalnızca deneysel (escore) ve küratörlü veritabanı (dscore) kanıtı kullanılır. STRING'in metin madenciliği (tscore)
ve birlikte anılma kanalları bilinçli olarak dışarıda bırakılır: bunlar PubMed'den türetildiği için literatür
kanalının bilgisini ağ kanalına sızdırır ve "bağımsız kanıt" iddiasını geçersiz kılar.

İki puan birleştirilir: s = 1 − (1 − e)(1 − d) (STRING'in kendi olasılık birleştirmesiyle aynı mantık).
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Tuple

import requests

from kesif_engine.core.net import HttpClient

log = logging.getLogger("kesif")

BASE = "https://string-db.org/api/json"
SPECIES = 9606
MIN_SCORE = 0.4        # birleşik deneysel+veritabanı puanı alt sınırı
PARTNER_LIMIT = 200    # tohum gen başına komşu sayısı
CALLER = "sagliktan-kesif"


def edge_score(item: dict) -> float:
    e = float(item.get("escore") or 0.0)
    d = float(item.get("dscore") or 0.0)
    return 1.0 - (1.0 - e) * (1.0 - d)


def parse_edges(items: Iterable[dict], min_score: float = MIN_SCORE) -> List[Tuple[str, str, float]]:
    """API satırları → (A, B, puan) listesi; semboller büyük harf, A < B, tekrarlar en yüksek puanla."""
    best: Dict[Tuple[str, str], float] = {}
    for it in items or []:
        a = str(it.get("preferredName_A") or "").upper()
        b = str(it.get("preferredName_B") or "").upper()
        if not a or not b or a == b:
            continue
        s = edge_score(it)
        if s < min_score:
            continue
        key = (a, b) if a < b else (b, a)
        if s > best.get(key, 0.0):
            best[key] = s
    return [(a, b, round(s, 4)) for (a, b), s in sorted(best.items())]


def _chunks(seq: List[str], n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _partners_chunk(client: HttpClient, chunk: List[str], limit: int, skipped: List[str]) -> list:
    """Bir grup gen için komşular. STRING, gruptaki tanımlayıcıların hiçbiri eşlenemediğinde 404 döner; bu durumda
    grup ikiye bölünerek yeniden denenir, tek başına eşlenemeyen gen atlanır (bütün ağ kanalı tek gen yüzünden boşalmaz)."""
    try:
        resp = client.request("POST", f"{BASE}/interaction_partners", data={
            "identifiers": "\r".join(chunk), "species": SPECIES, "limit": limit, "required_score": 150,
            "caller_identity": CALLER})
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code not in (400, 404):
            raise
        if len(chunk) == 1:
            skipped.append(chunk[0])
            return []
        mid = len(chunk) // 2
        return _partners_chunk(client, chunk[:mid], limit, skipped) + _partners_chunk(client, chunk[mid:], limit, skipped)
    return resp.json() or []


def partners(client: HttpClient, symbols: Iterable[str], limit: int = PARTNER_LIMIT) -> List[Tuple[str, str, float]]:
    """Tohum genlerin birinci derece komşuları (deneysel/veritabanı kenarlarıyla)."""
    items: list = []
    skipped: List[str] = []
    syms = sorted({s.upper() for s in symbols if s})
    for chunk in _chunks(syms, 100):
        items.extend(_partners_chunk(client, chunk, limit, skipped))
    if skipped:
        log.info("  protein ağı: STRING'in tanımadığı %d gen atlandı (%s)", len(skipped), ", ".join(skipped[:10]))
    return parse_edges(items)


def fetch_subnetwork(client: HttpClient, seeds: Iterable[str], targets: Iterable[str]) -> List[Tuple[str, str, float]]:
    """Tohum genlerin ve ilaç hedeflerinin birinci derece komşularından oluşan alt ağ.

    Bütün insan ağını indirmek yerine ilgili bölge alınır. Tohum → ara gen → hedef biçimindeki iki adımlı yollar bu
    bölgede eksiksiz bulunur; ağ yürüyüşünde daha uzak yolların katkısı yeniden başlama olasılığı nedeniyle küçüktür.
    """
    seeds = sorted({s.upper() for s in seeds if s})
    targets = sorted({t.upper() for t in targets if t} - set(seeds))
    merged: Dict[Tuple[str, str], float] = {}
    for a, b, s in partners(client, seeds) + partners(client, targets):
        merged[(a, b)] = max(s, merged.get((a, b), 0.0))
    nodes = {a for a, _ in merged} | {b for _, b in merged}
    log.info("  protein ağı: %d düğüm, %d kenar (yalnızca deneysel + veritabanı kanıtı)", len(nodes), len(merged))
    return [(a, b, s) for (a, b), s in sorted(merged.items())]
