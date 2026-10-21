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
            Şema 4 (ilk elle denetimde %30 hata bulundu; nedenleri sistematikti):
              - Çalışma hedef hastalıkla ilgili olmalı: ClinicalTrials.gov'un atadığı MeSH kodları (ya da üst kodları)
                hedef hastalığı içermeli. Arama bulanık eşleşiyor (FMF → Behçet, hipertansiyon → pulmoner hipertansiyon,
                Parkinson → kanser çalışmaları); kod yoksa hastalık adı başlıkta ya da hastalık listesinde aranır.
              - Sağlıklı gönüllülerle faz 1, ilaç etkileşimi / farmakokinetik / biyoeşdeğerlik çalışmaları sayılmaz
                (midazolam, varfarin gibi ölçüm amaçlı ilaçlar "test edilen ilaç" değildir).
              - Nakil, CAR-T, gen ya da hücre tedavisi içeren çalışmalarda ilaçlar hazırlık/destek amaçlıdır; sayılmaz.
              - Sedasyon, anestezi, biyopsi, ön ilaçlama amaçlı çalışmalar sayılmaz.
            Şema 5 (ikinci denetimde %19 hata): MeSH kodu tek başına yetmez. ClinicalTrials.gov pulmoner hipertansiyona
              'Hypertension' kodu da atıyor, MeSH ağacı Sjögren'i romatoid artritin altına koyuyor. Çalışmanın hastalık
              listesinde ya da başlığında hastalığın adı (resources/disease_terms.json) geçmeli ve ad başka bir
              hastalığı belirtmemeli ("pulmonary hypertension"). Eşlik eden hastalık (anemi, depresyon, osteoporoz,
              aşı yanıtı, enfeksiyon profilaksisi) ve ilaç bırakma/azaltma çalışmaları da sayılmaz.
  trials_late : aynı kurallarla, yalnızca faz 2 ve üstü çalışmalar (ikinci, daha katı etiket).

Çıktı: data/<hastalık>/labels.json
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Optional, Sequence

from kesif_engine.core.net import HttpClient
from kesif_engine.extraction.drugmap import DrugIndex
from kesif_engine.scoring import drugspace
from kesif_engine.sources import pubmed
from kesif_engine.sources.chembl import canonical_ids
from kesif_engine.sources.clinicaltrials import fetch_trials, trial_counts

log = logging.getLogger(__name__)
_DOSE = re.compile(r"\b\d+(\.\d+)?\s*(mg|mcg|µg|ug|g|ml|%|iu|units?)\b.*$", re.I)
SCHEMA = 5


def fetch_therapy_pmids(client: HttpClient, disease_query: str, start_year: int, end_year: int) -> list:
    query = f'({disease_query}) AND "drug therapy"[Subheading]'
    return pubmed.search_by_year(client, query, start_year, end_year, until=pubmed.data_until())


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


HEALTHY = re.compile(r"\bhealthy\s+(\w+\s+){0,3}(volunteers?|subjects?|participants?|adults?|males?|females?|men|women)\b", re.I)
WITH_PATIENTS = re.compile(r"\bpatients?\b|\b(subjects?|participants?|adults?|men|women) with\b", re.I)
PK_STUDY = re.compile(
    r"drug[- ]drug interaction|\bDDI\b|interaction (study|trial)|pharmacokinetic interaction|on the pharmacokinetics of"
    r"|relative bioavailability|bioequivalence|food effect|mass balance|\bcocktail\b|thorough QT|\bCYP\s?\d|cytochrome P450"
    r"|single[- ]dose pharmacokinetics of|\bprobe (drug|substrate)|tryptophan depletion|airway responsiveness|bronchoprovocation"
    r"|\bdeposition\b|\bdisposition\b|blood levels|drug levels|therapeutic drug monitoring", re.I)
PROCEDURAL = re.compile(
    r"\ban(a)?esthe|\bsedation\b|\bsedative\b|for (a |the )?(muscle |skin |renal )?biopsy|premedication|neuromuscular block|bowel prep"
    r"|colonoscopy|intubation|capsule endoscopy|endoscopy preparation|perioperative|intraoperative|post-induction"
    r"|infusion[- ](associated|related) reactions?|infusion reactions?|stellate ganglion|for imaging|nerve block|\bECMO\b|extracorporeal", re.I)
SUPPORT_CONTEXT = re.compile(
    r"transplant|stem[- ]cell|\bHSCT\b|\bCAR[- ]?T\b|chimeric antigen|gene therapy|\bAAV\d*\b|lymphodeplet|conditioning regimen"
    r"|\b(NK|T)[- ]cells?\b|cell therapy|\bcells\b", re.I)
# Müdahale adının kısaltılmış biçimleri ('GLP-1 analog' → 'GLP-1', 'Aldosterone antagonists' → 'Aldosterone') ancak
# atılan kısım doz/form bilgisiyse kullanılır; atılan kısım ilacın sınıfını ya da başka bir maddeyi tarif ediyorsa
# kısaltma yanlış ilaca eşler (denetimde görüldü: nikotinamid mononükleotid → niasinamid, Withania kökü → withanolid D).
CLASS_TAIL = re.compile(
    r"\b(inhibitors?|antagonists?|agonists?|analog(ue)?s?|blockers?|receptors?|mononucleotide|riboside|extracts?|roots?"
    r"|powder|leaf|leaves|flakes|oil|class|derivatives?|modulators?|activators?|mimetics?|conjugates?|vaccines?)\b", re.I)
# Tek başına yazıldığında bir ilaç değil ilaç SINIFI ya da enzim/hormon adı olan müdahaleler.
CLASS_NAMES = {"antihistamine", "antihistamines", "biguanide", "biguanides", "thiazolidinedione", "thiazolidinediones",
               "sulfonylurea", "sulfonylureas", "statin", "statins", "corticosteroid", "corticosteroids", "steroid",
               "steroids", "nsaid", "nsaids", "ppi", "glp-1", "neprilysin", "aldosterone", "angiotensin ii",
               "alpha-glucosidase inhibitor", "dpp-4 inhibitor", "sglt2 inhibitor", "insulin analog"}


COMORBID = re.compile(
    r"\ban(a)?emi|iron deficiency|\bdepress(ion|ive)\b|osteopor|bone (mineral )?density|bone loss|immunogenicity"
    r"|responses? to (the )?([\w-]+ )?vaccin|influenza vaccin|zoster vaccin|pneumococcal|covid-19 vaccin|sars-cov-2 vaccin"
    r"|hepatitis b|reactivation|clostridi|difficile|smoking|periodont|atheroscler|thromboprophylaxis"
    r"|venous thromboembolism|cardiotoxicity|bacterial overgrowth|prophylaxis|chemoprevention|colorectal (cancer|neoplasia)"
    r"|flushing|antiemetic|ovarian reserve|fertility preservation|\blipids?\b|cholesterol|carotid|atherosclerotic plaque|hyperuric"
    r"|bone health|skeletal|ototoxicity|meibomian|overactive bladder", re.I)
OTHER_DISEASE = re.compile(r"cancer|carcinoma|tumou?r|lymphoma|leuka?emia|melanoma|sarcoma|neoplasm|myeloma", re.I)
BLOOD_PRESSURE = re.compile(r"blood pressure|hypertensi", re.I)
DISCONTINUE = re.compile(r"\bdiscontinu|withdrawal of|\btaper(ing)?\b|de-?escalat|dose reduction|\bstopping\b", re.I)
TERMS_FILE = Path(__file__).resolve().parents[1] / "resources" / "disease_terms.json"


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"['’]s\b", "", text)).strip()


def disease_terms(name_en: str, mesh_id: str = "") -> list:
    """Hastalığın adları: elle derlenmiş sözlük (disease_terms.json) varsa o, yoksa adın doğal ve MeSH sırası."""
    return vocabulary(name_en, mesh_id)[0]


def vocabulary(name_en: str, mesh_id: str = "") -> tuple:
    """(terimler, dışlanan nitelemeler)."""
    try:
        table = json.loads(TERMS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        table = {}
    code = (mesh_id or "").split(":")[-1].strip().upper()
    entry = table.get(f"MESH:{code}") if code else None
    if entry:
        return [_norm(t) for t in entry.get("terms") or []], [_norm(t) for t in entry.get("exclude") or []]
    name = _norm(name_en)
    out = [name] if name else []
    if "," in name:
        head, tail = name.split(",", 1)
        out.insert(0, f"{tail.strip()} {head.strip()}")
    return out, []


def names_disease(text: str, terms: list, exclude: Sequence[str] = ()) -> bool:
    """Metin hedef hastalığı adlandırıyor mu? ('pulmonary hypertension' hipertansiyonu adlandırmaz.)"""
    t = _norm(text)
    if not t or any(x in t for x in exclude):
        return False
    return any(re.search(rf"\b{re.escape(term)}s?\b", t) for term in terms)


def trial_is_about(trial: dict, mesh_id: str, terms: list, exclude: Sequence[str] = ()) -> bool:
    """Çalışma hedef hastalığa mı ait? MeSH kodu (ya da üst kodu) tutmalı VE hastalık listesinden biri ya da başlık
    hastalığı adlandırmalı. Kod yoksa yalnızca ad. Terim listesi boşsa ad koşulu aranmaz."""
    code = (mesh_id or "").split(":")[-1].strip().upper()
    mesh = trial.get("condition_mesh")
    if code and mesh and code not in {str(m).upper() for m in mesh}:
        return False
    if not terms:
        return bool(code and mesh) or not code
    texts = list(trial.get("conditions") or []) + [trial.get("title") or ""]
    return any(names_disease(x, terms, exclude) for x in texts)


def exclusion_reason(trial: dict, terms: Sequence[str] = ()) -> str:
    """Çalışma etiket için uygun değilse nedeni; uygunsa ''. terms: hedef hastalığın adları (hedefe göre kurallar:
    kanser başlıklı çalışma kanser olmayan hedefe, kan basıncı çalışması hipertansiyon olmayan hedefe sayılmaz)."""
    title = trial.get("title") or ""
    target = " ".join(terms)
    mixed = WITH_PATIENTS.search(title)
    if (trial.get("healthy_volunteers") and _phase_rank(trial.get("phases")) <= 1 and not mixed) or (HEALTHY.search(title) and not mixed):
        return "healthy"
    if PK_STUDY.search(title):
        return "pk"
    if PROCEDURAL.search(title):
        return "procedural"
    if COMORBID.search(title):
        return "comorbidity"
    if target and BLOOD_PRESSURE.search(title) and not BLOOD_PRESSURE.search(target):
        return "comorbidity"
    if target and not OTHER_DISEASE.search(target) and OTHER_DISEASE.search(" ".join([title] + list(trial.get("conditions") or []))):
        return "other_disease"
    if DISCONTINUE.search(title):
        return "discontinuation"
    ints = trial.get("interventions") or []
    if SUPPORT_CONTEXT.search(title) or any(t == "GENETIC" or (t in ("PROCEDURE", "BIOLOGICAL") and SUPPORT_CONTEXT.search(n))
                                            for t, n in ints):
        return "support"
    return ""


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
        cands = intervention_candidates(name)
        if CLASS_TAIL.search(_DOSE.sub("", name)):
            cands = cands[:1]  # yalnızca tam ad; kısaltma sınıf/başka madde tarifini atar
        if _norm(cands[0] if cands else "") in CLASS_NAMES:
            continue
        hit = index.match(cands)
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
    # Birden çok kolda HER kolda bulunan ilaç, kollar arasında başka bir ilaç farkı varsa, karşılaştırılan değil ortak
    # zemindir (kolların tamamı "deneysel" işaretli olsa bile: idebenon + haloperidol / idebenon + deutetrabenazin).
    arm_sets = [{n.lower() for n in a.get("names") or []} for a in arms if a.get("names")]
    if len(arm_sets) >= 2:
        everywhere = set.intersection(*arm_sets)
        varying = {n for n in set.union(*arm_sets) - everywhere if not PLACEBO.search(n)}
        if varying:
            for k, v in list(found.items()):
                if v.lower() in everywhere:
                    comparator_ids.add(k)
    # aynı ilaç hem deneysel hem karşılaştırma kolunda (farklı yazımlarla) → arka plan tedavisi
    return {k: v for k, v in found.items() if k not in comparator_ids}


def map_trials_to_drugs(trials: list, drugs: list, min_phase: float, with_sources: bool = False, mesh_id: str = "",
                        name_en: str = "", stats: Optional[dict] = None):
    """Her ilaç (ana molekül) için hastalıkta ilk klinik çalışma yılı.

    Dönen: {chembl_id: yıl}; with_sources=True ise (yıllar, ileri faz yılları, kaynaklar) üçlüsü:
      ileri faz : faz 2 ve üstü çalışmalarla ilk yıl (ikinci, daha katı etiket)
      kaynaklar : {chembl_id: {"nct", "year", "name", "phase"}} — o yılı belirleyen çalışma (elle denetim için)
    Yalnızca girişimsel, tedavi/önleme amaçlı, MAX_CONDITIONS'tan az hastalık listeleyen, hedef hastalığa ait
    (trial_is_about) ve exclusion_reason'a takılmayan çalışmalar sayılır. stats verilirse elenme nedenleri sayılır.
    Tuz/ester biçimleri ana moleküle birleştirilir; sonuç her kayda (ana ve tuz) aynı yılı yazar.
    """
    eligible = [d for d in drugs if d["max_phase"] >= min_phase]
    index = DrugIndex(eligible)
    canonical = canonical_ids(drugs)
    excluded = {canonical.get(d["chembl_id"], d["chembl_id"]) for d in eligible
                if drugspace.classify(d, d.get("name", "")) == drugspace.KIND_DIAGNOSTIC}
    terms, exclude = vocabulary(name_en, mesh_id) if (name_en or mesh_id) else ([], [])
    first: dict = {}
    late: dict = {}
    sources: dict = {}
    for t in sorted(trials, key=lambda x: (x.get("start_year") or 9999, x.get("nct") or "")):
        if not t["start_year"] or not trial_counts(t):
            continue
        if len(t.get("conditions") or []) > MAX_CONDITIONS:
            continue
        reason = "" if trial_is_about(t, mesh_id, terms, exclude) else "other_disease"
        reason = reason or exclusion_reason(t, terms)
        if reason:
            if stats is not None:
                stats[reason] = stats.get(reason, 0) + 1
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
    stats: dict = {}
    first_trial, first_late, sources = (map_trials_to_drugs(trials, drugs, min_phase, with_sources=True,
                                                            mesh_id=disease_cfg.get("mesh_id", ""),
                                                            name_en=disease_cfg.get("name_en", ""), stats=stats)
                                        if drugs else ({}, {}, {}))
    log.info("Klinik çalışmalardan ilaca eşlenen müdahale: %d (faz 2+: %d); elenen çalışmalar: %s", len(first_trial),
             len(first_late), ", ".join(f"{k} {v}" for k, v in sorted(stats.items())) or "yok")
    labels = {
        "schema": SCHEMA,
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "therapy_pmids": therapy,
        "trials": trials,
        "first_trial_year": first_trial,
        "first_late_trial_year": first_late,
        "first_trial_source": sources,
        "excluded_trials": stats,
    }
    out_path.write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
    return labels


def load(path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}
