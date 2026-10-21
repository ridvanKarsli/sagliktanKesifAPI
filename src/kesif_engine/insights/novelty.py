"""Aday ilaç-hastalık çiftinin zaten bilinip bilinmediğini kontrol eder (ClinicalTrials.gov + PubMed)."""

from __future__ import annotations

import logging
from typing import Optional

from kesif_engine.core.net import HttpClient
from kesif_engine.sources import clinicaltrials, pubmed

log = logging.getLogger(__name__)


def trials_count(client: HttpClient, condition: str, intervention: str) -> int:
    return clinicaltrials.count(client, condition, intervention)


def pubmed_direct_count(client: HttpClient, disease_query: str, names) -> int:
    """Hastalık ve ilaç adlarından (ad + takma adlar) herhangi biri başlık/özette geçen makale sayısı."""
    if isinstance(names, str):
        names = [names]
    terms = " OR ".join(f'"{n}"[Title/Abstract]' for n in names)
    return pubmed.count(client, f"({disease_query}) AND ({terms})")


def status(trials: int, direct: int) -> str:
    if trials > 0:
        return "klinik çalışma kaydı var"
    if direct > 0:
        return "literatürde doğrudan geçiyor"
    return "doğrudan kayıt yok (aday)"


def _clean(name: str) -> str:
    """Sorguyu bozan karakterler (tırnak, köşeli parantez) atılır."""
    return " ".join(str(name).replace('"', " ").replace("[", " ").replace("]", " ").split())


def _names(row) -> list:
    names = [_clean(row["chemical"])]
    aliases = row.get("aliases")
    if isinstance(aliases, str):
        names += [_clean(a) for a in aliases.split(";") if len(a.strip()) >= 4]
    unique: list = []
    for name in names:
        if name and name not in unique:
            unique.append(name)
    return unique[:4]


def add_novelty(client: HttpClient, df, disease_cfg: dict, top_n: int = 50):
    head = df.head(top_n).copy()
    trials: list = []
    direct: list = []
    failed = 0
    for i, (_, row) in enumerate(head.iterrows(), 1):
        names = _names(row)
        # Tek bir adayın sorgusu başarısız olursa (tuhaf ad, servis hatası) yalnızca o aday "kontrol edilemedi" kalır;
        # önceden tek hata bütün yenilik kontrolünü düşürüyordu (gut, miyastenia gravis).
        t: Optional[int]
        d: Optional[int]
        try:
            t = trials_count(client, disease_cfg["name_en"], names[0])
            d = pubmed_direct_count(client, disease_cfg["pubmed_query"], names)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            if failed <= 3:
                log.warning("  yenilik kontrolü yapılamadı: %s (%s)", names[0], str(exc)[:120])
            t = d = None
        trials.append(t)
        direct.append(d)
        if i % 10 == 0:
            log.info("  yenilik kontrolü: %d/%d", i, len(head))
    if failed:
        log.warning("  %d/%d adayın yenilik kontrolü yapılamadı; bunlar 'kontrol edilmedi' görünecek.", failed, len(head))
    if failed == len(head) and len(head):
        raise RuntimeError("Hiçbir adayın yenilik kontrolü yapılamadı (servis yanıt vermiyor olabilir).")
    head["clinical_trials"] = trials
    head["pubmed_direct"] = direct
    head["durum"] = [status(t, d) if t is not None and d is not None else "" for t, d in zip(trials, direct)]
    return head
