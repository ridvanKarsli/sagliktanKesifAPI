"""Klinik çalışma kayıtları istemcisi (ClinicalTrials.gov v2): çalışma listesi, ayrıştırma, sayım."""

from __future__ import annotations

import logging
import re

from kesif_engine.core.net import HttpClient

log = logging.getLogger(__name__)
CT_URL = "https://clinicaltrials.gov/api/v2/studies"
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
        data = data or {}
        trials.extend(parse_trials(data))
        token = data.get("nextPageToken")
        if not token:
            break
    log.info("ClinicalTrials.gov: %d çalışma (%s)", len(trials), condition)
    return trials




def count(client: HttpClient, condition: str, intervention: str) -> int:
    """Hastalık + müdahale için kayıtlı çalışma sayısı."""
    params = {"query.cond": condition, "query.intr": intervention, "pageSize": 1, "countTotal": "true"}
    return int(client.request("GET", CT_URL, params=params).json().get("totalCount", 0))
