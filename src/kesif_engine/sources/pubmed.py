from __future__ import annotations

import datetime
import json
import re
from typing import Optional

from kesif_engine.core import config
from kesif_engine.core.net import HttpClient

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# Veri kesim gecikmesi: PubTator (gen/ilaç işaretlemesi) PubMed'in birkaç gün–birkaç hafta gerisinden gelir. Son
# LAG_DAYS günün makaleleri derleme alınmaz; böylece derlemin her makalesi işaretlenmiş olur, analiz eksik kalmaz ve
# aynı kesim tarihiyle tekrarlanabilir. Kesim tarihi derlem indeksine yazılır.
LAG_DAYS = 30


def data_until(today: Optional[datetime.date] = None) -> str:
    """Bugünden LAG_DAYS gün önceki tarih, PubMed biçiminde (YYYY/MM/DD)."""
    d = (today or datetime.date.today()) - datetime.timedelta(days=LAG_DAYS)
    return d.strftime("%Y/%m/%d")


def _params(extra: dict) -> dict:
    params = {"tool": "kesif"}
    if config.NCBI_EMAIL:
        params["email"] = config.NCBI_EMAIL
    if config.NCBI_API_KEY:
        params["api_key"] = config.NCBI_API_KEY
    params.update(extra)
    return params



def _json(resp) -> dict:
    """Katı olmayan JSON: PubMed yanıtı bazen ham denetim karakteri taşıyor (şizofreni tedavi sorgusunda görüldü)."""
    text = getattr(resp, "text", None)
    return json.loads(text, strict=False) if isinstance(text, str) else resp.json()


def search_pmids(
    client: HttpClient,
    query: str,
    retmax: int = 10000,
    mindate: Optional[str] = None,
    maxdate: Optional[str] = None,
    sort: Optional[str] = None,
) -> list:
    """PubMed'de arama yapar, PMID listesi döndürür (esearch tek istekte en fazla 10000)."""
    extra = {"db": "pubmed", "term": query, "retmode": "json", "retmax": retmax}
    if mindate and maxdate:
        extra.update({"datetype": "pdat", "mindate": mindate, "maxdate": maxdate})
    if sort:
        extra["sort"] = sort
    resp = client.request("GET", f"{EUTILS}/esearch.fcgi", params=_params(extra))
    return list(_json(resp)["esearchresult"].get("idlist", []))


def count(client: HttpClient, query: str) -> int:
    extra = {"db": "pubmed", "term": query, "retmode": "json", "retmax": 0}
    resp = client.request("GET", f"{EUTILS}/esearch.fcgi", params=_params(extra))
    result = _json(resp).get("esearchresult") or {}
    if "count" not in result:
        # PubMed sorguyu ayrıştıramazsa sayı yerine hata döndürür; 0 saymak "hiç makale yok" demek olur, yanlış
        raise ValueError(f"PubMed sorguyu işleyemedi: {str(result.get('ERROR') or result)[:120]}")
    return int(result["count"])


def count_by_year(client: HttpClient, query: str, start_year: int, end_year: int) -> dict:
    out = {}
    for year in range(start_year, end_year + 1):
        extra = {"db": "pubmed", "term": query, "retmode": "json", "retmax": 0, "datetype": "pdat", "mindate": str(year), "maxdate": str(year)}
        resp = client.request("GET", f"{EUTILS}/esearch.fcgi", params=_params(extra))
        out[year] = int(_json(resp)["esearchresult"]["count"])
    return out


def search_by_year(
    client: HttpClient,
    query: str,
    start_year: int,
    end_year: int,
    max_total: Optional[int] = None,
    until: Optional[str] = None,
) -> list:
    """Yıl yıl arar (esearch tek istekte en fazla 10000 sonuç verir).

    max_total verilirse ve toplam onu aşarsa, her yıldan makale sayısıyla orantılı bir kota alınır
    (alaka sırasına göre), böylece büyük hastalıklarda eski yıllar da temsil edilir ve geriye dönük
    test anlamlı kalır. max_total yoksa tüm makaleler alınır. until (YYYY/MM/DD) verilirse o tarihten sonraki
    makaleler alınmaz (veri kesim tarihi).
    """
    if until:
        end_year = min(end_year, int(until[:4]))
    seen: dict = {}
    counts = count_by_year(client, query, start_year, end_year) if max_total else {}
    total = sum(counts.values())
    sampled = bool(max_total) and total > max_total
    for year in range(end_year, start_year - 1, -1):
        lo, hi = f"{year}/01/01", (until if until and year == int(until[:4]) else f"{year}/12/31")
        if sampled:
            n_year = counts.get(year, 0)
            if n_year == 0:
                continue
            quota = max(1, int(round(max_total * n_year / total)))
            ids = search_pmids(client, query, min(quota, 10000), lo, hi, sort="relevance")
        else:
            ids = search_pmids(client, query, 10000, lo, hi)
        for pmid in ids:
            seen.setdefault(pmid, None)
    return list(seen)


def _year(text: str) -> Optional[int]:
    match = re.search(r"(19|20)\d{2}", text or "")
    return int(match.group(0)) if match else None


def fetch_meta(client: HttpClient, pmids: list) -> dict:
    """esummary ile başlık ve yayın yılını alır."""
    out: dict = {}
    for i in range(0, len(pmids), 200):
        batch = pmids[i : i + 200]
        extra = {"db": "pubmed", "id": ",".join(batch), "retmode": "json"}
        resp = client.request("POST", f"{EUTILS}/esummary.fcgi", data=_params(extra))
        result = _json(resp).get("result", {})
        for uid in result.get("uids", []):
            item = result[uid]
            year = _year(item.get("pubdate", "")) or _year(item.get("sortpubdate", ""))
            out[uid] = {"pmid": uid, "year": year, "title": item.get("title", "")}
    return out
