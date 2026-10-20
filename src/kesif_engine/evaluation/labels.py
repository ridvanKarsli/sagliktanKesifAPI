"""Geriye dönük doğrulama için tedavi odaklı etiketler.

"Hastalık makalesinde ilk kez anılan kimyasal" etiketi gürültülüdür (anestezik, kontrast, ayırıcı tanı
ilaçları da girer). Bu modül iki temiz etiket kaynağı sağlar:

  therapy : PubMed'de hastalık + "drug therapy"[Subheading] makaleleri (tedavi odaklı literatür)
  trials  : ClinicalTrials.gov'da hastalık için kayıtlı GİRİŞİMSEL, tedavi/önleme amaçlı çalışmaların DENEYSEL
            kolundaki ilaçlar ve başlangıç yılları. (Şema 2: önceki sürüm tüm kollardaki her müdahaleyi alıyordu;
            kalp yetersizliğinde doksorubisin/trastuzumab -kardiyotoksisite çalışmaları-, anestezi ajanları ve
            aktif karşılaştırma ilaçları "yeni klinik çalışmaya giren ilaç" sayılıyordu.)
            Şema 3: karşılaştırma/plasebo kolunda da bulunan ilaçlar (arka plan tedavisi), tanı ajanları ve yardımcı
            maddeler, 5'ten fazla hastalık listeleyen şemsiye çalışmalar sayılmaz; tuz biçimleri ana moleküle birleşir.
  trials_late : aynı kurallarla, yalnızca faz 2 ve üstü çalışmalar (ikinci, daha katı etiket).

Çıktı: data/<hastalık>/labels.json
"""

from __future__ import annotations

import datetime
import json
import logging
import re

from kesif_engine.core.net import HttpClient
from kesif_engine.extraction.drugmap import DrugIndex
from kesif_engine.scoring import drugspace
from kesif_engine.sources import pubmed
from kesif_engine.sources.chembl import canonical_ids
from kesif_engine.sources.clinicaltrials import fetch_trials, trial_counts

log = logging.getLogger(__name__)
_DOSE = re.compile(r"\b\d+(\.\d+)?\s*(mg|mcg|µg|ug|g|ml|%|iu|units?)\b.*$", re.I)
SCHEMA = 3


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


PLACEBO = re.compile(r"\b(placebo|sham|vehicle|dummy|saline|sugar pill|no (treatment|intervention)|usual care|standard (of )?care)\b", re.I)
COMPARATOR_ARMS = {"PLACEBO_COMPARATOR", "SHAM_COMPARATOR", "ACTIVE_COMPARATOR", "NO_INTERVENTION"}
DRUG_TYPES = {"", "DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT", "DIETARY_SUPPLEMENT"}
MAX_CONDITIONS = 5     # daha fazla hastalık listeleyen çalışmalar şemsiye/genel çalışmadır (ör. "kronik ağrı, astım, ...")
LATE_PHASES = {"PHASE2", "PHASE3", "PHASE4"}


def _phase_rank(phases) -> int:
    best = 0
    for ph in phases or []:
        ph = str(ph).upper()
        for n in (4, 3, 2, 1):
            if f"PHASE{n}" in ph:
                best = max(best, n)
                break
    return best


def trial_drugs(trial: dict, index: DrugIndex, canonical: dict, excluded: set) -> dict:
    """Bir çalışmada TEST EDİLEN ilaçlar: {chembl_id (ana molekül): müdahale adı}.

    Sayılmayanlar: plasebo/sahte/olağan bakım adları; karşılaştırma, plasebo ya da müdahalesiz kolda da bulunan ilaçlar
    (arka plan veya referans tedavi: "metotreksat + biyolojik" çalışmasında metotreksat); tanı ajanları ve yardımcı
    maddeler (excluded); kol bilgisi varsa deneysel kolda olmayan müdahaleler.
    """
    exp = {n.lower() for n in trial.get("experimental") or []}
    arms = trial.get("arms") or []
    in_comparator = {n.lower() for a in arms if a.get("type") in COMPARATOR_ARMS for n in a.get("names") or []}
    found: dict = {}
    comparator_ids = set()
    for itype, name in trial.get("interventions") or []:
        if itype not in DRUG_TYPES or PLACEBO.search(name):
            continue
        hit = index.match(intervention_candidates(name))
        if not hit:
            continue
        hit = canonical.get(hit, hit)
        if hit in excluded:
            continue
        low = name.lower()
        if low in in_comparator:
            comparator_ids.add(hit)
            continue
        if exp and low not in exp:
            continue
        found.setdefault(hit, name)
    # aynı ilaç hem deneysel hem karşılaştırma kolunda (farklı yazımlarla) → arka plan tedavisi
    return {k: v for k, v in found.items() if k not in comparator_ids}


def map_trials_to_drugs(trials: list, drugs: list, min_phase: float, with_sources: bool = False):
    """Her ilaç (ana molekül) için hastalıkta ilk klinik çalışma yılı.

    Dönen: {chembl_id: yıl}; with_sources=True ise (yıllar, ileri faz yılları, kaynaklar) üçlüsü:
      ileri faz : faz 2 ve üstü çalışmalarla ilk yıl (ikinci, daha katı etiket)
      kaynaklar : {chembl_id: {"nct", "year", "name", "phase"}} — o yılı belirleyen çalışma (elle denetim için)
    Yalnızca girişimsel, tedavi/önleme amaçlı ve MAX_CONDITIONS'tan az hastalık listeleyen çalışmalar sayılır.
    Tuz/ester biçimleri ana moleküle birleştirilir; sonuç her kayda (ana ve tuz) aynı yılı yazar.
    """
    eligible = [d for d in drugs if d["max_phase"] >= min_phase]
    index = DrugIndex(eligible)
    canonical = canonical_ids(drugs)
    excluded = {canonical.get(d["chembl_id"], d["chembl_id"]) for d in eligible
                if drugspace.classify(d, d.get("name", "")) == drugspace.KIND_DIAGNOSTIC}
    first: dict = {}
    late: dict = {}
    sources: dict = {}
    for t in sorted(trials, key=lambda x: (x.get("start_year") or 9999, x.get("nct") or "")):
        if not t["start_year"] or not trial_counts(t):
            continue
        if len(t.get("conditions") or []) > MAX_CONDITIONS:
            continue
        rank = _phase_rank(t.get("phases"))
        for hit, name in trial_drugs(t, index, canonical, excluded).items():
            if t["start_year"] < first.get(hit, 9999):
                first[hit] = t["start_year"]
                sources[hit] = {"nct": t.get("nct"), "year": t["start_year"], "name": name, "phase": rank}
            if rank >= 2 and t["start_year"] < late.get(hit, 9999):
                late[hit] = t["start_year"]
    # tuz biçimleri ana molekülün yılını taşır (sıralama hangi kimliği kullanırsa kullansın eşleşsin)
    for cid, parent in canonical.items():
        if parent in first and cid != parent:
            first[cid] = first[parent]
            if parent in late:
                late[cid] = late[parent]
    if with_sources:
        return first, late, sources
    return first


def build(client: HttpClient, disease_cfg: dict, drugs: list, min_phase: float, start_year: int, out_path) -> dict:
    this_year = datetime.date.today().year
    therapy = fetch_therapy_pmids(client, disease_cfg["pubmed_query"], start_year, this_year)
    log.info("Tedavi odaklı (drug therapy) makale: %d", len(therapy))
    trials = fetch_trials(client, disease_cfg["name_en"])
    first_trial, first_late, sources = map_trials_to_drugs(trials, drugs, min_phase, with_sources=True) if drugs else ({}, {}, {})
    log.info("Klinik çalışmalardan ilaca eşlenen müdahale: %d (faz 2+: %d)", len(first_trial), len(first_late))
    labels = {
        "schema": SCHEMA,
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "therapy_pmids": therapy,
        "trials": trials,
        "first_trial_year": first_trial,
        "first_late_trial_year": first_late,
        "first_trial_source": sources,
    }
    out_path.write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
    return labels


def load(path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}
