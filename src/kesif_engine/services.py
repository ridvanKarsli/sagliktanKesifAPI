"""Dış veri kaynaklarının sağlık kontrolü: her servise küçük, gerçek bir istek atılır ve yanıt biçimi doğrulanır.

Neden: Ekim 2026'da iki kez iş yarıda kaldı — PubTator çok yeni PMID'li grubu reddetti, ClinicalTrials.gov yeni
alan adlarını kabul etmedi. Bu kontrol, bir analize başlamadan ya da canlıda her gece, dört servisin hem erişilebilir
hem de beklenen biçimde yanıt verdiğini gösterir. Komut satırı: `kesif doctor`; arayüz: ana sayfadaki düğme;
API: GET /api/health/services.
"""

from __future__ import annotations

import time

from . import chembl, genes, labels, pubmed, pubtator
from .net import HttpClient

KNOWN_PMID = "29097191"   # voretigene neparvovec (RP) — PubTator'da işaretli
KNOWN_GENE = "7124"       # TNF
KNOWN_CHEMBL = "CHEMBL1201580"  # infliximab


def _timed(fn):
    t = time.monotonic()
    try:
        detail = fn()
        return {"ok": True, "detail": detail, "ms": int((time.monotonic() - t) * 1000)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:200]}", "ms": int((time.monotonic() - t) * 1000)}


def check_pubmed(client: HttpClient):
    n = pubmed.count(client, '"Behcet Syndrome"[MeSH Terms]')
    if n < 1000:
        raise RuntimeError(f"beklenmedik sayı: {n}")
    return f"makale araması çalışıyor ({n:,} Behçet makalesi)"


def check_pubtator_export(client: HttpClient):
    recs = pubtator.annotate(client, [KNOWN_PMID])
    rec = recs.get(KNOWN_PMID, {})
    if not rec.get("genes") and not rec.get("chemicals"):
        raise RuntimeError("işaretleme boş döndü")
    return f"makale işaretleme çalışıyor ({len(rec.get('genes', {}))} gen, {len(rec.get('chemicals', {}))} kimyasal, {len(rec.get('relations', []))} ilişki)"


def check_pubtator_count(client: HttpClient):
    data = client.request("GET", genes.PUBTATOR_SEARCH, params={"text": f"@GENE_{KNOWN_GENE}", "page_size": 1}).json()
    n = genes.parse_count(data)
    if not n or n < 10000:
        raise RuntimeError(f"sayı alanı bulunamadı ya da beklenmedik: {n} (anahtarlar: {list(data)[:6] if isinstance(data, dict) else type(data).__name__})")
    return f"gen sayımı çalışıyor (TNF: {n:,} makale)"


def check_chembl(client: HttpClient):
    data = client.request("GET", f"{chembl.BASE}/molecule/{KNOWN_CHEMBL}", params={"format": "json"}).json()
    mol = chembl.parse_molecule(data)
    if not mol.get("name"):
        raise RuntimeError("molekül adı yok")
    return f"ilaç veritabanı çalışıyor ({mol['name'].lower()}, faz {mol['max_phase']:.0f}, ATC {', '.join(mol['atc']) or 'yok'})"


def check_clinicaltrials(client: HttpClient):
    params = {"query.cond": "Behcet Syndrome", "pageSize": 2, "format": "json", "fields": labels.CT_FIELD_SETS[0]}
    data = client.request("GET", labels.CT_URL, params=params).json()
    trials = labels.parse_trials(data)
    if not trials:
        raise RuntimeError("çalışma listesi boş")
    with_arms = sum(1 for t in trials if t.get("experimental") or t.get("study_type"))
    return f"klinik çalışma kayıtları çalışıyor ({len(trials)} kayıt, {with_arms} tanesinde tasarım/kol bilgisi)"


# Arayüzde kaynakların ticari/teknik adları gösterilmez (genel adlar); ayrıntı günlükte ve komut satırında.
CHECKS = [
    ("pubmed", "Makale veritabanı", check_pubmed),
    ("pubtator_export", "Makale işaretleme", check_pubtator_export),
    ("pubtator_count", "Gen literatür sayımı", check_pubtator_count),
    ("chembl", "İlaç veritabanı", check_chembl),
    ("clinicaltrials", "Klinik çalışma kayıtları", check_clinicaltrials),
]


def check_all(client: HttpClient = None) -> list:
    client = client or HttpClient(timeout=30, retries=1)
    out = []
    for key, name, fn in CHECKS:
        res = _timed(lambda fn=fn: fn(client))
        res.update({"key": key, "name": name})
        out.append(res)
    return out
