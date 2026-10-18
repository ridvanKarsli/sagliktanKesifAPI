"""İlaç uzayı: bir ChEMBL kaydının yeniden konumlandırma adayı olarak hangi sınıfa girdiği.

Sekiz hastalıkta (Behçet, FMF, RP, tip 2 diyabet, hipertansiyon, kalp yetersizliği, astım, romatoid artrit)
aday listelerinin kılavuzlarla karşılaştırılması üç tekrar eden sorun gösterdi:

  1. Endojen moleküller (anjiyotensin, aldosteron, endotelin, nitrik oksit, C-peptid, insülin, ghrelin...)
     en üst sıralara çıkıyordu. Bunlar hastalık makalelerinde ÖLÇÜLEN biyobelirteçlerdir; ilaç olarak
     kullanılmaları ayrı bir konudur. Sınıf: "endojen" — varsayılan görünümde gizli, test evreninde yok.
  2. Tanı/provokasyon ajanları, kontrast ve boyalar, elektrolit çözeltileri (metakolin astımda, floresein
     RP'de, kalsiyum, dekstran...) aday görünüyordu. Sınıf: "tanı/yardımcı" — tamamen dışarıda.
  3. Vitamin, amino asit, yağ asidi ve bitkisel moleküller (kurkumin, kersetin, taurin...) ilaçlarla aynı
     kefede sıralanıyordu. Sınıf: "besin/takviye" — listede kalır ama ayrı etiketlenir.

Sınıflandırma sırası: ad listeleri (config) > ATC sınıfı (ChEMBL) > gen ürünü eşleşmesi (ilacın adı, literatürde
gen/protein olarak etiketlenen bir adla aynıysa: "insulin", "glucagon", "angiotensin II") > ChEMBL natural_product.
"""

from __future__ import annotations

import re
from typing import Optional

from kesif_engine.core import config

KIND_DRUG = "ilaç"
KIND_ENDOGENOUS = "endojen"
KIND_NUTRIENT = "besin/takviye"
KIND_DIAGNOSTIC = "tanı/yardımcı"

_SUFFIXES = (" human", " (human)", " recombinant", " (recombinant)", " acetate", " sodium", " hydrochloride",
             " chloride", " sulfate", " alfa", " beta")


def norm(name: str) -> str:
    s = (name or "").lower().strip()
    s = s.replace("‐", "-")
    s = re.sub(r"\s+", " ", s)
    return s


def _variants(name: str) -> set:
    """Ad varyantları: tire/boşluk farkı, 'human'/'recombinant' ekleri, 'ii'/'-1' sonekleri."""
    base = norm(name)
    out = {base, base.replace("-", " "), base.replace(" ", "-")}
    for suf in _SUFFIXES:
        if base.endswith(suf):
            core = base[: -len(suf)].strip()
            out.update({core, core.replace("-", " ")})
    out = {o for o in out if o}
    return out


def gene_product_names(records) -> set:
    """Kayıtlardaki gen/protein etiketlerinin normalize kümesi (ilaç adıyla karşılaştırmak için)."""
    names = set()
    for rec in records:
        for label in rec.genes.values():
            if label:
                names.add(norm(label).replace("-", " "))
    return names


def _atc_excluded(atc: list) -> bool:
    codes = [c for c in (atc or []) if c]
    if not codes:
        return False
    return all(c.upper().startswith(config.EXCLUDED_ATC_PREFIXES) for c in codes)


def classify(drug: dict, label: str = "", gene_names: Optional[set] = None) -> str:
    """Bir ilacın sınıfı. drug: ChEMBL kaydı (name, atc, type, natural_product, max_phase). label: makalelerdeki ad."""
    drug = drug or {}
    names = set()
    for n in (drug.get("name"), label):
        if n:
            names |= _variants(n)
    for s in drug.get("synonyms") or []:
        if len(s) >= 5:
            names.add(norm(s))
    name_main = {v for n in (drug.get("name"), label) if n for v in _variants(n)}

    if name_main & config.DIAGNOSTIC_NAMES or name_main & config.STOP_CHEMICALS:
        return KIND_DIAGNOSTIC
    if _atc_excluded(drug.get("atc") or []):
        return KIND_DIAGNOSTIC
    if name_main & config.ENDOGENOUS:
        return KIND_ENDOGENOUS
    if gene_names:
        # ilacın adı literatürde gen/protein olarak etiketlenen bir adla aynı: ürünün kendisi (insulin, glucagon,
        # angiotensin II, endothelin-1, albumin, C-peptide). Kısa kısaltmalar (ra, pg) karşılaştırılmaz.
        for v in name_main:
            vv = v.replace("-", " ")
            if len(vv) >= 5 and vv in gene_names:
                return KIND_ENDOGENOUS
    if name_main & config.NUTRIENT_NAMES:
        return KIND_NUTRIENT
    # ChEMBL natural_product bayrağı kullanılmıyor: omapatrilat, fenretinide, zaprinast gibi sentetik ilaçları da
    # işaretliyor (gerçek veride görüldü); sınıf yalnızca ad listeleriyle belirlenir.
    return KIND_DRUG


def in_backtest_universe(kind: str) -> bool:
    """Güvenilirlik testinde hangi sınıflar sayılır: ilaç ve besin/takviye (klinik çalışmaya girebilirler);
    endojen ve tanı ajanları sayılmaz (hastalıkla anılmaları ölçüm ilişkisidir, etiket gürültüsü yaratır)."""
    return kind in (KIND_DRUG, KIND_NUTRIENT)
