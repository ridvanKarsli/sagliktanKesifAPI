"""Aday ilaç-hastalık çiftinin zaten bilinip bilinmediğini kontrol eder (ClinicalTrials.gov + PubMed)."""

from __future__ import annotations

import logging

from . import pubmed
from .net import HttpClient

log = logging.getLogger(__name__)
CT_URL = "https://clinicaltrials.gov/api/v2/studies"


def trials_count(client: HttpClient, condition: str, intervention: str) -> int:
    params = {"query.cond": condition, "query.intr": intervention, "pageSize": 1, "countTotal": "true"}
    resp = client.request("GET", CT_URL, params=params)
    return int(resp.json().get("totalCount", 0))


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


def _names(row) -> list:
    names = [str(row["chemical"])]
    aliases = row.get("aliases")
    if isinstance(aliases, str):
        names += [a.strip() for a in aliases.split(";") if len(a.strip()) >= 4]
    unique: list = []
    for name in names:
        if name and name not in unique:
            unique.append(name)
    return unique[:4]


def add_novelty(client: HttpClient, df, disease_cfg: dict, top_n: int = 50):
    head = df.head(top_n).copy()
    trials, direct = [], []
    for i, (_, row) in enumerate(head.iterrows(), 1):
        names = _names(row)
        trials.append(trials_count(client, disease_cfg["name_en"], names[0]))
        direct.append(pubmed_direct_count(client, disease_cfg["pubmed_query"], names))
        if i % 10 == 0:
            log.info("  yenilik kontrolü: %d/%d", i, len(head))
    head["clinical_trials"] = trials
    head["pubmed_direct"] = direct
    head["durum"] = [status(t, d) for t, d in zip(trials, direct)]
    return head
