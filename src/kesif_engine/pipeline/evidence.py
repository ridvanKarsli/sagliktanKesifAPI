"""Literatürden bağımsız kanıt adımı: genetik ilişki (Open Targets) ve protein ağı yakınlığı (STRING + RWR).

Çıktı: data/<hastalık>/evidence_external.json

    {"genetic": {SEMBOL: {veri_türü: skor}}, "network": {SEMBOL: z}, "meta": {...}}

Dış servislerden biri yanıt vermezse adım düşmez: o kanal boş kalır, meta'ya hata yazılır ve sıralama mevcut
kanallarla sürer. Bu kanalların ağırlığı yoktur; birleştirmeye girip girmeyeceğine benchmark ölçümü karar verir.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Optional

from kesif_engine.catalog import diseases
from kesif_engine.core import config
from kesif_engine.core.net import HttpClient
from kesif_engine.core.paths import workdir
from kesif_engine.pipeline.status import _mark
from kesif_engine.scoring import network
from kesif_engine.scoring.features import drug_targets
from kesif_engine.sources import chembl, genes, opentargets, string_ppi

log = logging.getLogger("kesif")

FILE = "evidence_external.json"


def load_external(disease: str) -> dict:
    path = workdir(disease) / FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _bridge_symbols(disease: str) -> list:
    index_path = workdir(disease) / "corpus_index.json"
    if not index_path.exists():
        return []
    index = json.loads(index_path.read_text(encoding="utf-8"))
    info = genes.load_cache()
    out = set()
    for gid, source in (index.get("gene_source") or {}).items():
        if source != "primary":
            continue
        sym = (info.get(gid) or {}).get("symbol")
        if sym:
            out.add(sym.upper())
    return sorted(out)


def step_evidence(disease: str, client: Optional[HttpClient] = None) -> dict:
    cfg = diseases.get(disease)
    client = client or HttpClient(timeout=60, retries=2)
    meta: dict = {"at": datetime.datetime.now().isoformat(timespec="seconds")}
    genetic: dict = {}

    log.info("[1/2] Genetik ilişki kanıtı alınıyor...")
    try:
        ot = opentargets.fetch(client, cfg.get("name_en") or cfg.get("label") or disease, cfg.get("mesh_id", ""))
        genetic = ot.get("targets") or {}
        meta.update({"disease_match": ot.get("disease"), "n_targets": len(genetic), "n_genetic": ot.get("n_genetic", 0),
                     "excluded_types": ot.get("excluded_types")})
        if ot.get("note"):
            meta["genetic_note"] = ot["note"]
    except Exception as exc:  # noqa: BLE001
        log.warning("Genetik kanıt alınamadı (%s); bu kanal boş kalacak.", exc)
        meta["genetic_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    log.info("[2/2] Protein ağı yakınlığı hesaplanıyor...")
    z_targets: dict = {}
    drugs = [d for d in chembl.load() if d.get("max_phase", 0) >= config.MIN_PHASE]
    targets = sorted({t for d in drugs for t in drug_targets(d)})
    seeds, seed_source = network.choose_seeds(genetic, _bridge_symbols(disease))
    meta.update({"seed_source": seed_source, "n_seeds": len(seeds), "n_drug_targets": len(targets)})
    if seeds and targets:
        try:
            edges = string_ppi.fetch_subnetwork(client, seeds, targets)
            z = network.proximity(edges, seeds)
            z_targets = {t: z[t] for t in targets if z.get(t, 0) > 0}
            meta.update({"n_edges": len(edges), "n_proximal_targets": len(z_targets)})
        except Exception as exc:  # noqa: BLE001
            log.warning("Protein ağı alınamadı (%s); ağ kanalı boş kalacak.", exc)
            meta["network_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    elif not targets:
        meta["network_note"] = "İlaç sözlüğü yok; önce ilaç sözlüğü adımı çalışmalı."

    out = {"genetic": genetic, "network": z_targets, "meta": meta}
    (workdir(disease) / FILE).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    summary = {"n_genetic": meta.get("n_genetic", 0), "n_network": len(z_targets), "seed_source": seed_source,
               "errors": [k for k in ("genetic_error", "network_error") if k in meta]}
    _mark(disease, "evidence", **summary)
    log.info("Bağımsız kanıt: %d genetik gen, ağda yakın %d ilaç hedefi (tohum: %s)", summary["n_genetic"], len(z_targets), seed_source)
    return summary
