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

from . import pubmed
from .drugmap import DrugIndex
from .net import HttpClient

log = logging.getLogger(__name__)
CT_URL = "https://clinicaltrials.gov/api/v2/studies"
_DOSE = re.compile(r"\b\d+(\.\d+)?\s*(mg|mcg|µg|ug|g|ml|%|iu|units?)\b.*$", re.I)
SCHEMA = 2
# Alan listesi: API bir ad kümesini reddederse (HTTP 400) sırayla sonrakine düşülür. En sonuncusu eski (şema 1) listedir;
# kol bilgisi gelmez ama iş durmaz (etiket kalitesi düşer, günlükte uyarı verilir).
CT_FIELD_SETS = [
    "protocolSection.identificationModule.nctId,protocolSection.statusModule.startDateStruct,protocolSection.designModule,protocolSection.armsInterventionsModule",
    "NCTId,StartDate,StudyType,DesignPrimaryPurpose,Phase,ArmGroupType,ArmGroupInterventionNames,InterventionName,InterventionType",
    "NCTId,StartDate,StudyType,DesignPrimaryPurpose,InterventionName,InterventionType",
    "NCTId,StartDate,InterventionName,InterventionType",
]
CT_FIELDS = CT_FIELD_SETS[0]
# Tedavi ya da önleme amaçlı çalışmalar sayılır; tanı, temel bilim, tarama, sağlık hizmeti, cihaz çalışmaları sayılmaz.
PURPOSES_OK = {"TREATMENT", "PREVENTION", ""}
ARM_EXPERIMENTAL = {"EXPERIMENTAL"}


def fetch_therapy_pmids(client: HttpClient, disease_query: str, start_year: int, end_year: int) -> list:
    query = f'({disease_query}) AND "drug therapy"[Subheading]'
    return pubmed.search_by_year(client, query, start_year, end_year)


def _year(date_str) -> int:
    m = re.match(r"(\d{4})", str(date_str or ""))
    return int(m.group(1)) if m else 0


def _strip_arm_prefix(name: str) -> str:
    """'Drug: Apremilast' -> 'Apremilast'."""
    return re.sub(r"^[A-Za-z _]+:\s*", "", name or "").strip()


def parse_trials(payload: dict) -> list:
    """ClinicalTrials.gov v2 yanıtından çalışma kayıtları:
    {nct, start_year, study_type, purpose, phases, interventions:[(type, name)], experimental:[name]}

    experimental: DENEYSEL kollarda kullanılan müdahale adları (kol bilgisi yoksa boş; eşleme tüm müdahalelere düşer).
    """
    out = []
    for study in payload.get("studies", []):
        ps = study.get("protocolSection", {}) or {}
        nct = (ps.get("identificationModule") or {}).get("nctId", "")
        start = ((ps.get("statusModule") or {}).get("startDateStruct") or {}).get("date")
        design = ps.get("designModule") or {}
        ami = ps.get("armsInterventionsModule") or {}
        ints = []
        for iv in ami.get("interventions") or []:
            name = (iv.get("name") or "").strip()
            if name:
                ints.append(((iv.get("type") or "").upper(), name))
        experimental = []
        for arm in ami.get("armGroups") or []:
            if (arm.get("type") or "").upper() in ARM_EXPERIMENTAL:
                for n in arm.get("interventionNames") or []:
                    n = _strip_arm_prefix(n)
                    if n and n not in experimental:
                        experimental.append(n)
        out.append({
            "nct": nct,
            "start_year": _year(start),
            "study_type": (design.get("studyType") or "").upper(),
            "purpose": ((design.get("designInfo") or {}).get("primaryPurpose") or "").upper(),
            "phases": [str(x).upper() for x in (design.get("phases") or [])],
            "interventions": ints,
            "experimental": experimental,
        })
    return out


def trial_counts(trial: dict) -> bool:
    """Bu çalışma 'ilaç bu hastalık için klinik çalışmaya girdi' etiketine sayılır mı?"""
    if trial.get("study_type") and trial["study_type"] != "INTERVENTIONAL":
        return False
    return (trial.get("purpose") or "") in PURPOSES_OK


def _ct_page(client: HttpClient, condition: str, fields: str, token):
    params = {"query.cond": condition, "pageSize": 1000, "format": "json", "fields": fields}
    if token:
        params["pageToken"] = token
    return client.request("GET", CT_URL, params=params).json()


def fetch_trials(client: HttpClient, condition: str) -> list:
    import requests

    trials, token, fields = [], None, None
    while True:
        if fields is None:
            # ilk sayfada alan kümesini seç: API'nin kabul ettiği ilk küme
            data = None
            for i, candidate in enumerate(CT_FIELD_SETS):
                try:
                    data = _ct_page(client, condition, candidate, token)
                    fields = candidate
                    if i > 0:
                        log.warning("ClinicalTrials.gov alan kümesi %d kabul edilmedi; %d. küme kullanılıyor%s", i, i + 1,
                                    " (kol bilgisi yok: etiketler tüm müdahaleleri sayar)" if i == len(CT_FIELD_SETS) - 1 else "")
                    break
                except requests.HTTPError as exc:
                    if exc.response is not None and exc.response.status_code == 400 and i < len(CT_FIELD_SETS) - 1:
                        continue
                    raise
        else:
            data = _ct_page(client, condition, fields, token)
        trials.extend(parse_trials(data))
        token = data.get("nextPageToken")
        if not token:
            break
    log.info("ClinicalTrials.gov: %d çalışma (%s)", len(trials), condition)
    return trials


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
