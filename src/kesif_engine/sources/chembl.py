"""ChEMBL'den klinik aşamadaki ilaçların sözlüğünü kurar: ad, eş adlar, faz, etki mekanizması ve hedef genler.

Kaynak: https://www.ebi.ac.uk/chembl/api/data  (ücretsiz, anahtar gerekmez)
Çıktı: data/drugs/chembl_drugs.json
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from kesif_engine.core import config
from kesif_engine.core.net import HttpClient

log = logging.getLogger(__name__)

BASE = "https://www.ebi.ac.uk/chembl/api/data"
PAGE = 1000


def _phase(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def _paged(client: HttpClient, path: str, key: str, params: dict):
    offset = 0
    while True:
        query = dict(params)
        query.update({"format": "json", "limit": PAGE, "offset": offset})
        data = client.request("GET", f"{BASE}/{path}", params=query).json()
        items = data.get(key, [])
        yield from items
        meta = data.get("page_meta", {})
        offset += PAGE
        if not meta.get("next") or not items or offset >= int(meta.get("total_count", 0)):
            break


def parse_molecule(mol: dict) -> dict:
    syns = []
    for s in mol.get("molecule_synonyms") or []:
        name = (s.get("molecule_synonym") or "").strip()
        if name:
            syns.append(name)
    hierarchy = mol.get("molecule_hierarchy") or {}
    return {
        "chembl_id": mol.get("molecule_chembl_id"),
        "name": (mol.get("pref_name") or "").strip(),
        "max_phase": _phase(mol.get("max_phase")),
        "type": mol.get("molecule_type"),
        "first_approval": mol.get("first_approval"),
        "parent": hierarchy.get("parent_chembl_id") or mol.get("molecule_chembl_id"),
        "synonyms": sorted(set(syns)),
        # ATC sınıfı (tanı ajanı/kontrast/elektrolit ayıklaması) ve doğal ürün bayrağı (besin/takviye sınıfı)
        "atc": sorted({c for c in (mol.get("atc_classifications") or []) if c}),
        "natural_product": 1 if str(mol.get("natural_product") or "0") == "1" else 0,
        "mechanisms": [],
    }


def has_classification(drugs: list) -> bool:
    """Sözlük ATC bilgisiyle mi kuruldu? Eski sözlüklerde alan yok; 'drugs' adımı yenile ile tekrar kurulmalı."""
    return any("atc" in d for d in drugs[:50])


def parse_target_genes(target: dict) -> list:
    genes = []
    for comp in target.get("target_components") or []:
        for syn in comp.get("target_component_synonyms") or []:
            if syn.get("syn_type") == "GENE_SYMBOL" and syn.get("component_synonym"):
                genes.append(syn["component_synonym"].strip())
    return sorted(set(genes))


def assemble(molecules, mechanisms, targets: dict) -> list:
    """Molekülleri mekanizma ve hedef genlerle birleştirir. targets: target_chembl_id -> {name, genes}."""
    drugs = {m["chembl_id"]: m for m in molecules if m.get("chembl_id")}
    by_mol: dict = {}
    for mech in mechanisms:
        mol_id = mech.get("molecule_chembl_id")
        tgt = targets.get(mech.get("target_chembl_id") or "", {})
        entry = {
            "action_type": (mech.get("action_type") or "").upper(),
            "moa": mech.get("mechanism_of_action") or "",
            "target": tgt.get("name", ""),
            "genes": tgt.get("genes", []),
        }
        by_mol.setdefault(mol_id, []).append(entry)
        parent = mech.get("parent_molecule_chembl_id")
        if parent and parent != mol_id:
            by_mol.setdefault(parent, []).append(entry)
    for drug in drugs.values():
        mechs = by_mol.get(drug["chembl_id"]) or by_mol.get(drug["parent"]) or []
        seen = set()
        for m in mechs:
            key = (m["action_type"], m["moa"], tuple(m["genes"]))
            if key not in seen:
                seen.add(key)
                drug["mechanisms"].append(m)
    return sorted(drugs.values(), key=lambda d: (-d["max_phase"], d["name"]))


def dictionary_path() -> Path:
    path = config.DATA_DIR / "drugs" / "chembl_drugs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def build(client: HttpClient, min_phase: float = 1.0) -> list:
    log.info("ChEMBL molekülleri indiriliyor (max_phase >= %s)...", min_phase)
    molecules = [parse_molecule(m) for m in _paged(client, "molecule", "molecules", {"max_phase__gte": min_phase})]
    log.info("  %d molekül", len(molecules))
    log.info("ChEMBL mekanizmaları indiriliyor...")
    mechanisms = list(_paged(client, "mechanism", "mechanisms", {}))
    log.info("  %d mekanizma kaydı", len(mechanisms))
    target_ids = sorted({m.get("target_chembl_id") for m in mechanisms if m.get("target_chembl_id")})
    log.info("%d hedef için gen sembolleri alınıyor...", len(target_ids))
    targets: dict = {}
    for i in range(0, len(target_ids), 50):
        batch = target_ids[i : i + 50]
        for t in _paged(client, "target", "targets", {"target_chembl_id__in": ",".join(batch)}):
            targets[t["target_chembl_id"]] = {"name": t.get("pref_name", ""), "genes": parse_target_genes(t)}
    drugs = assemble(molecules, mechanisms, targets)
    payload = {
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "min_phase": min_phase,
        "n_drugs": len(drugs),
        "drugs": drugs,
    }
    dictionary_path().write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    log.info("İlaç sözlüğü yazıldı: %s (%d ilaç)", dictionary_path(), len(drugs))
    return drugs


def load() -> list:
    path = dictionary_path()
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("drugs", [])


# ---------- endikasyonlar (ilacın bugün ne için kullanıldığı) ----------

def indications_path() -> Path:
    path = config.DATA_DIR / "drugs" / "indications.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def fetch_indications(client: HttpClient, chembl_id: str) -> list:
    """ChEMBL drug_indication: [{term, phase}] — en yüksek fazdan düşüğe, en fazla 12. Sonuç önbelleğe yazılır."""
    path = indications_path()
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if chembl_id in cache:
        return cache[chembl_id]
    items = []
    for ind in _paged(client, "drug_indication", "drug_indications", {"molecule_chembl_id": chembl_id}):
        term = (ind.get("efo_term") or ind.get("mesh_heading") or "").strip()
        if term:
            items.append({"term": term, "phase": _phase(ind.get("max_phase_for_ind"))})
    seen, out = set(), []
    for it in sorted(items, key=lambda x: -float(x["phase"])):
        term = str(it["term"]).lower()
        if term not in seen:
            seen.add(term)
            out.append(it)
    cache[chembl_id] = out[:12]
    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache[chembl_id]
