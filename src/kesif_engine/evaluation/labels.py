"""Geriye dönük doğrulama için tedavi odaklı etiketler.

"Hastalık makalesinde ilk kez anılan kimyasal" etiketi gürültülüdür (anestezik, kontrast, ayırıcı tanı
ilaçları da girer). Bu modül iki temiz etiket kaynağı sağlar:

  therapy : PubMed'de hastalık + "drug therapy"[Subheading] makaleleri (tedavi odaklı literatür)
  trials  : ClinicalTrials.gov'da hastalık için kayıtlı GİRİŞİMSEL, tedavi/önleme amaçlı çalışmaların DENEYSEL
            kolundaki ilaçlar ve başlangıç yılları. (Şema 2: önceki sürüm tüm kollardaki her müdahaleyi alıyordu;
            kalp yetersizliğinde doksorubisin/trastuzumab -kardiyotoksisite çalışmaları-, anestezi ajanları ve
            aktif karşılaştırma ilaçları "yeni klinik çalışmaya giren ilaç" sayılıyordu.)

Çıktı: data/<hastalık>/labels.json
"""

from __future__ import annotations

import datetime
import json
import logging
import re

from kesif_engine.core.net import HttpClient
from kesif_engine.extraction.drugmap import DrugIndex
from kesif_engine.sources import pubmed
from kesif_engine.sources.clinicaltrials import fetch_trials, trial_counts

log = logging.getLogger(__name__)
_DOSE = re.compile(r"\b\d+(\.\d+)?\s*(mg|mcg|µg|ug|g|ml|%|iu|units?)\b.*$", re.I)
SCHEMA = 2


def fetch_therapy_pmids(client: HttpClient, disease_query: str, start_year: int, end_year: int) -> list:
    query = f'({disease_query}) AND "drug therapy"[Subheading]'
    return pubmed.search_by_year(client, query, start_year, end_year)


def intervention_candidates(name: str) -> list:
    """'Apremilast 30 mg BID' -> ['apremilast 30 mg bid', 'apremilast'] gibi deneme adları."""
    base = name.strip()
    cleaned = _DOSE.sub("", base).strip(" ,;:-")
    cleaned = re.split(r"\s*[\(\[/+,;]\s*", cleaned)[0].strip()
    words = cleaned.split()
    cands = [base, cleaned]
    if len(words) >= 2:
        cands.append(" ".join(words[:2]))
    if words:
        cands.append(words[0])
    seen, out = set(), []
    for c in cands:
        c = c.strip()
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def map_trials_to_drugs(trials: list, drugs: list, min_phase: float) -> dict:
    """Her ChEMBL ilacı için hastalıkta ilk klinik çalışma yılı: {chembl_id: year}.

    Yalnızca girişimsel, tedavi/önleme amaçlı çalışmalar; kol bilgisi varsa yalnızca deneysel koldaki ilaçlar
    (plasebo ve aktif karşılaştırma kolları sayılmaz). Kol bilgisi olmayan eski kayıtlarda tüm ilaç müdahaleleri.
    """
    index = DrugIndex([d for d in drugs if d["max_phase"] >= min_phase])
    first: dict = {}
    for t in trials:
        if not t["start_year"] or not trial_counts(t):
            continue
        exp = {n.lower() for n in t.get("experimental") or []}
        for itype, name in t["interventions"]:
            if itype and itype not in ("DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT", "DIETARY_SUPPLEMENT"):
                continue
            if exp and name.lower() not in exp:
                continue
            hit = index.match(intervention_candidates(name))
            if hit:
                first[hit] = min(first.get(hit, 9999), t["start_year"])
    return first


def build(client: HttpClient, disease_cfg: dict, drugs: list, min_phase: float, start_year: int, out_path) -> dict:
    this_year = datetime.date.today().year
    therapy = fetch_therapy_pmids(client, disease_cfg["pubmed_query"], start_year, this_year)
    log.info("Tedavi odaklı (drug therapy) makale: %d", len(therapy))
    trials = fetch_trials(client, disease_cfg["name_en"])
    first_trial = map_trials_to_drugs(trials, drugs, min_phase) if drugs else {}
    log.info("Klinik çalışmalardan ilaca eşlenen müdahale: %d", len(first_trial))
    labels = {
        "schema": SCHEMA,
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "therapy_pmids": therapy,
        "trials": trials,
        "first_trial_year": first_trial,
    }
    out_path.write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
    return labels


def load(path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}
