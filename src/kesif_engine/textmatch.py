"""Makale metninde PubTator'ın etiketlemediği ilaç adlarını sözlükle tarar.

PubTator'ın kimyasal etiketleyicisi küçük molekülleri iyi yakalar ama (1) etanercept, anakinra, interferon alfa-2a
gibi protein/antikor ilaçları çoğu zaman etiketlemez (Behçet derlemesinde başlığında "etanercept" geçen 44
makalede sıfır etiket); (2) MeSH'e henüz girmemiş yeni küçük moleküller (baxdrostat, retatrutide, lorundrostat)
de etiketsiz kalır. Bu modül ChEMBL'deki biyolojik ilaçların ve yeni/araştırma aşamasındaki küçük moleküllerin
adlarını makale metninde kelime sınırlı olarak arar; bulduklarını "CHEMBL:<id>" kimliğiyle kimyasal listesine ekler.
"""

from __future__ import annotations

import datetime
import re
from typing import Optional

from . import config

BIOLOGIC_TYPES = {"Protein", "Antibody", "Oligosaccharide", "Oligonucleotide", "Enzyme", "Cell", "Gene"}
MIN_LEN = 6
NEW_SMALL_MOLECULE_YEARS = 8   # son N yılda onaylanan ya da henüz onaylanmamış (faz 1-3) küçük moleküller
_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-]*")


def is_new_small_molecule(d: dict, this_year: Optional[int] = None) -> bool:
    """Tek kelimelik, INN görünümlü, yeni ya da araştırma aşamasındaki küçük molekül (metin taraması adayı)."""
    if d.get("type") != "Small molecule":
        return False
    name = (d.get("name") or "").strip().lower()
    if len(name) < 7 or " " in name or not name.isalpha():
        return False
    this_year = this_year or datetime.date.today().year
    phase = d.get("max_phase", -1) or 0
    approved = d.get("first_approval")
    if phase >= 4:
        return bool(approved) and int(approved) >= this_year - NEW_SMALL_MOLECULE_YEARS
    return phase >= 1


def _norm(name: str) -> str:
    return re.sub(r"[\s\-]+", " ", name.lower().strip())


class DrugTextIndex:
    """Ad -> chembl_id sözlüğü; metin kelime n-gramlarıyla (1-4) eşlenir."""

    def __init__(self, drugs: list, min_phase: Optional[float] = None, types=BIOLOGIC_TYPES, new_small_molecules: bool = True):
        min_phase = config.MIN_PHASE if min_phase is None else min_phase
        self.names: dict = {}
        self.label: dict = {}
        self.max_n = 1
        for d in drugs:
            if d.get("max_phase", -1) < min_phase:
                continue
            if d.get("type") in types:
                names = [d.get("name", "")] + list(d.get("synonyms", []))
            elif new_small_molecules and is_new_small_molecule(d):
                names = [d.get("name", "")]  # yalnızca tercih edilen ad; eş adlar (kod adları) gürültülü
            else:
                continue
            for name in names:
                key = _norm(name)
                if len(key) < MIN_LEN or key in config.STOP_CHEMICALS or key in config.ENDOGENOUS or not re.search(r"[a-z]", key):
                    continue
                n = len(key.split())
                if n > 4:
                    continue
                self.max_n = max(self.max_n, n)
                self.names.setdefault(key, d["chembl_id"])
                self.label.setdefault(d["chembl_id"], d.get("name", "").lower())

    def find(self, text: str) -> dict:
        """{chembl_id: görülen ad} — metinde geçen biyolojik ilaçlar."""
        if not text or not self.names:
            return {}
        toks = _TOKEN.findall(text.lower().replace("‐", "-"))
        toks = [t.replace("-", " ") for t in toks]
        found: dict = {}
        for i in range(len(toks)):
            for n in range(1, self.max_n + 1):
                if i + n > len(toks):
                    break
                key = " ".join(toks[i : i + n])
                hit = self.names.get(key)
                if hit:
                    found.setdefault(hit, key)
        return found


# Vücutta zaten bulunan ve ChEMBL'de ürün olarak kayıtlı moleküller: config.ENDOGENOUS (tek kaynak).
ENDOGENOUS = config.ENDOGENOUS


def enrich(records, index: DrugTextIndex) -> int:
    """Kayıtlardaki kimyasal listesine metinden bulunan biyolojikleri ekler; eklenen toplam sayısını döndürür.

    Aynı makalede PubTator'ın gen/protein olarak etiketlediği bir ad (ör. 'prothrombin', 'insulin') ilaç değil
    endojen protein anımıdır; atlanır. ENDOGENOUS listesindekiler her durumda atlanır.
    """
    added = 0
    for rec in records:
        text = getattr(rec, "text", "") or ""
        if not text:
            continue
        gene_labels = {_norm(l) for l in rec.genes.values()}
        for chembl_id, seen in index.find(text).items():
            label = index.label.get(chembl_id, seen)
            if seen in gene_labels or label in gene_labels or label in ENDOGENOUS or seen in ENDOGENOUS:
                continue
            cid = f"CHEMBL:{chembl_id}"
            if cid not in rec.chemicals:
                rec.chemicals[cid] = label
                added += 1
    return added
