"""Hastalık tanımları: kökteki diseases.json (varsayılanlar) + data/diseases.json (kullanıcı eklemeleri).

Yeni hastalık, adından MeSH terimi ve kimliği çözümlenerek eklenir (NCBI E-utilities, db=mesh).
"""

from __future__ import annotations

import json
import re
import unicodedata

from kesif_engine.core import config
from kesif_engine.core.net import HttpClient
from kesif_engine.sources.pubmed import EUTILS, _params


def _user_path():
    path = config.DATA_DIR / "diseases.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _removed_path():
    return config.DATA_DIR / "diseases_removed.json"


def load_removed() -> set:
    path = _removed_path()
    return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()


# Uygulama katmanı hastalık tanımlarını veritabanından sağlayabilir: set_provider(lambda: {key: cfg, ...}).
# Sağlayıcı yoksa (komut satırı/tek başına kullanım) paket kataloğu + data/diseases.json okunur.
_provider = None


def set_provider(fn) -> None:
    global _provider
    _provider = fn


def load_all() -> dict:
    if _provider is not None:
        return dict(_provider())
    out = dict(config.load_diseases())
    path = _user_path()
    if path.exists():
        out.update(json.loads(path.read_text(encoding="utf-8")))
    for key in load_removed():
        out.pop(key, None)
    return out


def remove(key: str, purge: bool = False) -> dict:
    """Hastalığı listeden kaldırır; purge=True ise indirilen veri ve sonuç klasörlerini de siler.

    Kökteki varsayılan hastalıklar (kodla gelenler) da kaldırılabilir: anahtar 'kaldırılanlar' listesine yazılır.
    Kullanıcı eklemesi ise data/diseases.json'dan silinir. Yeniden eklenirse kaldırılanlar listesinden çıkar.
    """
    import shutil

    path = _user_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if key in data:
        data.pop(key)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if key in config.load_diseases():
        removed = load_removed() | {key}
        _removed_path().write_text(json.dumps(sorted(removed), ensure_ascii=False), encoding="utf-8")
    deleted = []
    if purge:
        for base in (config.DATA_DIR, config.RESULTS_DIR):
            target = base / key
            if target.exists() and target.is_dir():
                shutil.rmtree(target)
                deleted.append(str(target))
    return {"removed": key, "purged": deleted}


def get(key: str) -> dict:
    diseases = load_all()
    if key not in diseases:
        raise KeyError(f"Bilinmeyen hastalık '{key}'. Tanımlı olanlar: {', '.join(diseases)}")
    return diseases[key]


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "hastalik"


def build_query(mesh_term: str, name_en: str) -> str:
    terms = []
    if mesh_term:
        terms.append(f'"{mesh_term}"[MeSH Terms]')
    if name_en and name_en.lower() != (mesh_term or "").lower():
        terms.append(f'"{name_en}"[Title/Abstract]')
    elif mesh_term:
        terms.append(f'"{mesh_term}"[Title/Abstract]')
    return " OR ".join(terms)


def parse_mesh_summary(result: dict) -> list:
    """esummary db=mesh sonucundan [{uid, term, mesh_id, tree}] listesi."""
    out = []
    for uid in result.get("uids", []):
        item = result.get(uid, {}) or {}
        terms = item.get("ds_meshterms") or []
        term = terms[0] if terms else item.get("ds_meshui") or ""
        ui = item.get("ds_meshui") or ""
        out.append({"uid": uid, "term": term, "mesh_id": f"MESH:{ui}" if ui else "", "tree": item.get("ds_idxlinks", [])})
    return out


def resolve_mesh(client: HttpClient, name: str, limit: int = 5) -> list:
    """Hastalık adını MeSH'te arar; olası eşleşmeleri döndürür."""
    resp = client.request("GET", f"{EUTILS}/esearch.fcgi", params=_params({"db": "mesh", "term": name, "retmode": "json", "retmax": limit}))
    ids = resp.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    resp = client.request("POST", f"{EUTILS}/esummary.fcgi", data=_params({"db": "mesh", "id": ",".join(ids), "retmode": "json"}))
    return parse_mesh_summary(resp.json().get("result", {}))


def add(key: str, label: str, name_en: str, mesh_id: str = "", mesh_term: str = "", pubmed_query: str = "") -> dict:
    key = slugify(key or name_en)
    entry = {
        "label": label or name_en,
        "name_en": name_en,
        "mesh_id": mesh_id,
        "pubmed_query": pubmed_query or build_query(mesh_term, name_en),
    }
    path = _user_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[key] = entry
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    removed = load_removed()
    if key in removed:
        removed.discard(key)
        _removed_path().write_text(json.dumps(sorted(removed), ensure_ascii=False), encoding="utf-8")
    return {key: entry}
