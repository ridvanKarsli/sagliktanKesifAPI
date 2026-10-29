"""Klinik çalışma atlası: ClinicalTrials.gov'daki tüm girişimsel ilaç çalışmalarından "hangi ilaç, hangi hastalıkta,
ilk kez hangi yıl denendi" tablosu; "benzer hastalıklarda denenmiş" kanalının (komşu hastalık kanıtı) kaynağı.

Neden: bir ilacın hastalıkla ilgili literatürü olmasa da, kesitten önce benzer hastalıklarda denenmiş olması güçlü ve
hastalığın kendi literatüründen bağımsız bir işarettir. Benzerlik de yine kesitten önce iki hastalıkta denenmiş ilaç
kümelerinden hesaplanır; kanal bu yüzden kesit yılına göre TAM dilimlenir (genetik ve ağ kanallarından farkı budur).

Atlası derleyen fonksiyon etiket kurallarını kullandığı için değerlendirme katmanındadır (evaluation.atlas.build).

Dosyalar (config.DATA_DIR altında):
    trials_raw/ct_interventional.jsonl.gz   indirilen ham çalışmalar (yalnızca yerel; büyük, veritabanına yüklenmez)
    trials_raw/download_state.json          kaldığı yerden sürdürme durumu
    trials/atlas.json.gz                    derlenmiş atlas (paylaşılan kapsam: veritabanında saklanır)

Atlas biçimi: {"schema", "built_at", "n_trials", "names": {MeSH: ad}, "first_year": {MeSH: {chembl_id: yıl}},
              "failed": {"efficacy"|"safety": {MeSH: {chembl_id: ilk sonlandırma yılı}}},
              "failed_nct": aynı biçimde o yılın NCT kimliği}
"failed": etkisizlik ya da güvenlik nedeniyle erken sonlandırılan (TERMINATED/SUSPENDED) çalışmalar; yıl, sonlandırma
(bitiş) tarihidir, böylece başarısızlık bilgisi de kesite göre dilimlenir.
Hastalık anahtarı çalışmaya ClinicalTrials.gov'un atadığı DOĞRUDAN MeSH kodlarıdır (üst kodlar değil: "Eklem
hastalıkları" gibi geniş üst terimler her eklem hastalığına komşu görünür ve benzerliği anlamsızlaştırır).
"""

from __future__ import annotations

import datetime
import gzip
import json
import logging
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from kesif_engine.core import config
from kesif_engine.core.net import HttpClient
from kesif_engine.sources.clinicaltrials import CT_URL, _json, parse_trials

log = logging.getLogger(__name__)

SCHEMA = 3  # 2: ilk yılı belirleyen çalışmanın NCT kimliği (first_nct); 3: erken sonlandırılan çalışmalar (failed)
FIELDS = ("protocolSection.identificationModule,protocolSection.statusModule.startDateStruct,"
          "protocolSection.statusModule.studyFirstSubmitDate,protocolSection.statusModule.studyFirstPostDateStruct,"
          "protocolSection.statusModule.overallStatus,protocolSection.statusModule.whyStopped,"
          "protocolSection.statusModule.completionDateStruct,"
          "protocolSection.designModule,"
          "protocolSection.armsInterventionsModule,protocolSection.conditionsModule,"
          "protocolSection.eligibilityModule.healthyVolunteers,derivedSection.conditionBrowseModule")
# Yalnız ilaç türü müdahale içeren girişimsel çalışmalar. API filtreyi reddederse (HTTP 400) sıradakine düşülür;
# en sonuncusu yalnız girişimsel filtresidir, ilaç türü ayıklaması yerelde yapılır.
FILTERS = [
    "AREA[StudyType]INTERVENTIONAL AND AREA[InterventionType](DRUG OR BIOLOGICAL OR DIETARY_SUPPLEMENT OR COMBINATION_PRODUCT)",
    "AREA[StudyType]INTERVENTIONAL",
]
PAGE_SIZE = 1000
NEIGHBORS = 50          # en benzer kaç hastalık kullanılır
MIN_DRUGS = 3           # komşu sayılması için kesitten önce en az bu kadar ilaç denenmiş olmalı


def raw_dir() -> Path:
    p = config.DATA_DIR / "trials_raw"
    p.mkdir(parents=True, exist_ok=True)
    return p


def atlas_path() -> Path:
    p = config.DATA_DIR / "trials"
    p.mkdir(parents=True, exist_ok=True)
    return p / "atlas.json.gz"


def _compact(t: dict) -> dict:
    """Atlas için gereken alanlar (ham dosyayı küçük tutmak için)."""
    keep = ("nct", "title", "start_year", "study_type", "purpose", "phases", "interventions", "experimental", "arms",
            "conditions", "condition_mesh", "condition_mesh_direct", "healthy_volunteers", "first_submitted", "first_posted", "start_date",
            "status", "why_stopped", "completion_date")
    return {k: t.get(k) for k in keep}


def _dates(payload: dict) -> Dict[str, Tuple[str, str, str]]:
    """nct → (kayda ilk gönderilme, ilk yayımlanma, başlangıç tarihi). Tahmin defteri takibi gün çözünürlüğünde ve İLK
    YAYIMLANMA tarihine göre yapılır: gönderilen kayıt ClinicalTrials.gov'da ancak denetimden sonra (günler–haftalar)
    görünür; dondurma anında kamuya açık olmayan bir çalışma, gönderim tarihi önce olsa bile tahminin bilemeyeceği
    bilgidir."""
    out = {}
    for st in payload.get("studies", []):
        ps = st.get("protocolSection") or {}
        sm = ps.get("statusModule") or {}
        nct = (ps.get("identificationModule") or {}).get("nctId", "")
        out[nct] = (str(sm.get("studyFirstSubmitDate") or ""), str((sm.get("studyFirstPostDateStruct") or {}).get("date") or ""),
                    str((sm.get("startDateStruct") or {}).get("date") or ""))
    return out


def _status(payload: dict) -> Dict[str, Tuple[str, str, str]]:
    """nct → (genel durum, durdurma gerekçesi, bitiş/sonlandırma tarihi). Sonlandırılan çalışmanın bitiş tarihi
    sonlandırma tarihidir; başarısızlık bilgisi kesit dilimlemesinde bu tarihe göre kullanılır."""
    out = {}
    for st in payload.get("studies", []):
        ps = st.get("protocolSection") or {}
        sm = ps.get("statusModule") or {}
        nct = (ps.get("identificationModule") or {}).get("nctId", "")
        out[nct] = (str(sm.get("overallStatus") or ""), str(sm.get("whyStopped") or ""),
                    str((sm.get("completionDateStruct") or {}).get("date") or ""))
    return out


def _direct_mesh(payload: dict) -> Dict[str, List[Tuple[str, str]]]:
    """nct → çalışmanın doğrudan MeSH kodları; ayrıca ad sözlüğü için (kod, ad) çiftleri."""
    out = {}
    for st in payload.get("studies", []):
        nct = ((st.get("protocolSection") or {}).get("identificationModule") or {}).get("nctId", "")
        browse = (st.get("derivedSection") or {}).get("conditionBrowseModule") or {}
        out[nct] = [(str(m.get("id")), m.get("term") or "") for m in browse.get("meshes") or [] if m.get("id")]
    return out


def download(client: Optional[HttpClient] = None, progress: Optional[Callable[[str], None]] = None,
             restart: bool = False) -> dict:
    """Tüm girişimsel ilaç çalışmalarını sayfa sayfa indirir; kesilirse kaldığı sayfadan sürer."""
    client = client or HttpClient(timeout=120)
    say = progress or (lambda m: log.info(m))
    out = raw_dir() / "ct_interventional.jsonl.gz"
    state_path = raw_dir() / "download_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() and not restart else {}
    if state.get("done"):
        say(f"Ham çalışmalar zaten indirilmiş ({state.get('n', 0)} çalışma); yeniden indirmek için restart=True.")
        return state
    part = out.with_name(out.name + ".part")  # indirme bitene kadar ayrı dosyaya yazılır, sonunda tek adımda yerine konur
    if not state:
        part.unlink(missing_ok=True)
        state = {"token": None, "n": 0, "pages": 0, "filter": None, "started": datetime.datetime.now().isoformat(timespec="seconds")}
    import requests

    mode = "at" if state["n"] and part.exists() else "wt"
    if mode == "wt":
        state.update({"token": None, "n": 0, "pages": 0})
    with gzip.open(part, mode, encoding="utf-8") as fh:
        while True:
            params = {"pageSize": PAGE_SIZE, "format": "json", "fields": FIELDS}
            if state["token"]:
                params["pageToken"] = state["token"]
            data = None
            filters = [state["filter"]] if state["filter"] else FILTERS
            for i, flt in enumerate(filters):
                try:
                    data = _json(client.request("GET", CT_URL, params={**params, "filter.advanced": flt}))
                    if not data.get("studies") and not state["n"] and i < len(filters) - 1:
                        continue  # filtre kabul edildi ama hiçbir şey eşleşmedi: sözdizimi farklı, sonrakine geç
                    state["filter"] = flt
                    break
                except requests.HTTPError as exc:
                    if exc.response is not None and exc.response.status_code == 400 and i < len(filters) - 1:
                        continue
                    raise
            data = data or {}
            direct = _direct_mesh(data)
            dates = _dates(data)
            status = _status(data)
            for t in parse_trials(data):
                t["condition_mesh_direct"] = direct.get(t["nct"], [])
                t["first_submitted"], t["first_posted"], t["start_date"] = dates.get(t["nct"], ("", "", ""))
                t["status"], t["why_stopped"], t["completion_date"] = status.get(t["nct"], ("", "", ""))
                fh.write(json.dumps(_compact(t), ensure_ascii=False) + "\n")
                state["n"] += 1
            state["pages"] += 1
            state["token"] = data.get("nextPageToken")
            fh.flush()
            state_path.write_text(json.dumps(state))
            if state["pages"] % 10 == 0:
                say(f"  {state['n']:,} çalışma indirildi ({state['pages']} sayfa)")
            if not state["token"]:
                break
    n_written = sum(1 for _ in iter_raw(part))
    if n_written != state["n"]:
        raise RuntimeError(f"Ham dosya eksik yazıldı: {n_written} satır, beklenen {state['n']}. İndirme yeniden başlatılmalı.")
    part.replace(out)
    state["done"] = True
    state["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    state_path.write_text(json.dumps(state))
    say(f"İndirme bitti: {state['n']:,} çalışma")
    return state


def iter_raw(path: Optional[Path] = None) -> Iterable[dict]:
    path = path or raw_dir() / "ct_interventional.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


class TrialAtlas:
    """Derlenmiş atlas üzerinde komşu hastalık puanı."""

    def __init__(self, atlas: dict):
        self.first_year: Dict[str, Dict[str, int]] = atlas.get("first_year") or {}
        self.names: Dict[str, str] = atlas.get("names") or {}
        self.first_nct: Dict[str, Dict[str, str]] = atlas.get("first_nct") or {}
        # {gerekçe türü: {MeSH: {chembl_id: ilk sonlandırma yılı}}}; şema < 3 ise boş
        self.failed: Dict[str, Dict[str, Dict[str, int]]] = atlas.get("failed") or {}
        self.failed_nct: Dict[str, Dict[str, Dict[str, str]]] = atlas.get("failed_nct") or {}
        self._stopped_index: Optional[Dict[str, List[Tuple[str, str, int, str]]]] = None
        self.meta = {k: atlas.get(k) for k in ("schema", "built_at", "n_trials")}
        self._cache: Dict[int, Dict[str, Set[str]]] = {}

    def source(self, code: str, chembl_id: str) -> Tuple[Optional[int], str]:
        """(ilk yıl, o yılın NCT kimliği) — kanıt zinciri için; atlas şema 1 ise NCT boş."""
        return self.first_year.get(code, {}).get(chembl_id), self.first_nct.get(code, {}).get(chembl_id, "")

    def failed_before(self, cutoff: int, kind: str = "efficacy") -> Dict[str, Set[str]]:
        """{chembl_id: kesit yılına kadar bu gerekçeyle erken sonlandırılan çalışmalarının hastalıkları (MeSH)}."""
        out: Dict[str, Set[str]] = {}
        for code, row in (self.failed.get(kind) or {}).items():
            for c, y in row.items():
                if y <= cutoff:
                    out.setdefault(c, set()).add(code)
        return out

    def stopped(self, chembl_id: str) -> List[Tuple[str, str, int, str]]:
        """İlacın erken sonlandırılan çalışmaları: [(gerekçe türü, MeSH, yıl, NCT)], yeniden eskiye."""
        if self._stopped_index is None:
            idx: Dict[str, List[Tuple[str, str, int, str]]] = {}
            for kind, by_code in self.failed.items():
                for code, row in by_code.items():
                    for c, y in row.items():
                        idx.setdefault(c, []).append((kind, code, y, self.failed_nct.get(kind, {}).get(code, {}).get(c, "")))
            for v in idx.values():
                v.sort(key=lambda x: (-x[2], x[0], x[1]))
            self._stopped_index = idx
        return self._stopped_index.get(chembl_id, [])

    def tried_before(self, cutoff: int) -> Dict[str, Set[str]]:
        """{MeSH: kesit yılı dahil o yıla kadar denenmiş ilaçlar}; yalnız MIN_DRUGS ve üstü."""
        if cutoff not in self._cache:
            sets = {}
            for code, row in self.first_year.items():
                s = {c for c, y in row.items() if y <= cutoff}
                if len(s) >= MIN_DRUGS:
                    sets[code] = s
            self._cache[cutoff] = sets
        return self._cache[cutoff]

    def neighbor_scores(self, own_first_year: Dict[str, int], own_codes: Iterable[str], cutoff: Optional[int] = None,
                        k: int = NEIGHBORS, canonical: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, float], List[dict]]:
        """İlaç başına komşu hastalık puanı ve kullanılan komşular.

        own_first_year: hedef hastalıkta ilaçların ilk çalışma yılı (hastalığın kendi etiketleri); yalnız ≤ kesit kullanılır.
        own_codes: hedef hastalığın MeSH kodları (komşu sayılmaz).
        puan(ilaç) = Σ_{en benzer k hastalık E} Jaccard(D, E) · [ilaç E'de ≤ kesit yılında denendi]
        """
        cutoff = cutoff if cutoff is not None else datetime.date.today().year
        own = {c for c, y in (own_first_year or {}).items() if y and y <= cutoff}
        if canonical:
            own = {canonical.get(c, c) for c in own}
        if not own:
            return {}, []
        skip = {str(c).split(":")[-1].upper() for c in own_codes if c}
        sims = []
        for code, s in self.tried_before(cutoff).items():
            if code.upper() in skip:
                continue
            inter = len(own & s)
            if inter:
                sims.append((inter / (len(own) + len(s) - inter), code))
        sims.sort(key=lambda x: (-x[0], x[1]))
        top = sims[:k]
        score: Dict[str, float] = {}
        sets = self.tried_before(cutoff)
        for sim, code in top:
            for c in sets[code]:
                score[c] = score.get(c, 0.0) + sim
        if canonical:  # tuz biçimleri ana molekülün puanını taşır
            for cid, parent in canonical.items():
                if parent in score and cid not in score:
                    score[cid] = score[parent]
        used = [{"mesh": code, "name": self.names.get(code, ""), "similarity": round(sim, 4)} for sim, code in top[:10]]
        return {c: round(v, 6) for c, v in score.items()}, used


def load(path: Optional[Path] = None) -> Optional[TrialAtlas]:
    path = path or atlas_path()
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return TrialAtlas(json.load(fh))


def similarity_note() -> str:
    return "benzer hastalıklarda kesitten önce denenmiş ilaçlar (ClinicalTrials.gov atlası; tam zaman dilimli)"

