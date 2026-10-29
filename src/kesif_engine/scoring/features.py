"""İlaç başına kanıt kanalları: her ilaç için tek satırlık, kanallar arası karşılaştırılabilir özellik tablosu.

Aynı fonksiyon hem canlı sıralamada hem geriye dönük testte kullanılır; ölçülen ile kullanılan hiçbir zaman ayrışmaz.

Kanallar (hepsi "büyük = daha güçlü kanıt"):

    lit         dolaylı literatür bağı: hastalık → köprü gen → ilaç (Swanson ABC; ranking.rank_candidates)
    mech        ilacın bilinen hedefi bir köprü gen mi (ilaç veritabanı mekanizması)
    direct      ilaç hastalık literatüründe doğrudan kaç makalede geçiyor (log)
    recent      bu doğrudan anılmaların son RECENT_YEARS yıldaki kısmı (log): yükselen ilgi
    accel       kanıt ivmesi: son RECENT_YEARS yıldaki anılma, önceki anılmalara göre ne kadar hızlı artıyor
                ((1 + yeni) / √(1 + eski); yeni anılma yoksa 0). Temiz etiketli ölçümde klinik çalışmaya giren ilaçların
                %63'ü kesitten önce hastalık literatüründe zaten anılıyordu; bunlar arasında belirleyici olan toplam
                anılma (popülerlik) değil, ilginin son yıllardaki hızlanmasıydı.
    genetic     ilaç hedeflerinin hastalıkla genetik ilişkisi (en güçlü hedef; 0–1)
    biology     hedeflerin genetik dışı bağımsız biyolojik kanıtı: hayvan modeli, gen ifadesi, yolak (0–1)
    network     hedeflerin protein ağında hastalık genlerine yakınlığı (derece eşlemeli z-skoru, en yakın hedef)
    similar     komşu hastalık kanıtı: ilaç, bu hastalığa en benzer hastalıklarda kesit yılından önce klinik çalışmaya
                girmiş mi (benzerlik ağırlıklı toplam; sources.trial_atlas). Benzerlik de kesitten önce iki hastalıkta
                denenmiş ilaç kümelerinden hesaplanır: kanal tam zaman dilimlidir ve hastalığın kendi literatüründen
                bağımsızdır. 48 hastalıklık panelde tek başına AP 0,122 (sıklık 0,079).

Ek sütunlar (sıralama kanalı değil, açıklayıcı/denetim): base (mevcut yöntem puanı), penalty (zarar sinyali çarpanı),
frequency (ilacın hastalık ve köprü gen literatüründe toplam anılma sayısı: popülerlik; geriye dönük testteki
"sıklık" taban çizgisiyle birebir aynı sayım),
ltc (ilacı hastalığa bağlayan farklı köprü gen sayısı; literatür tabanlı keşifte yayımlanmış "bağlantı terimi sayısı"
taban çizgisi),
prevalence (ilacın köprü gen literatüründeki yaygınlığı; "her yerde geçen ilaç" ölçüsü), n_targets.

Genetik ve ağ kanallarında "bilinen ilaç" ve "literatür" kaynaklı kanıt bilinçli olarak kullanılmaz: ilki hastalıkta
zaten denenmiş ilaçları kodlar (cevabı önceden görmek olur), ikincisi literatür kanalıyla aynı bilgiyi tekrarlar.
"""

from __future__ import annotations

import datetime
import json
import math
from collections import defaultdict
from typing import Dict, Iterable, Optional

import pandas as pd

RECENT_YEARS = 5
CHANNELS = ("lit", "mech", "direct", "recent", "accel", "genetic", "biology", "network", "similar")
GENETIC_TYPES = ("genetic_association", "somatic_mutation")
BIOLOGY_TYPES = ("animal_model", "rna_expression", "affected_pathway")


def drug_targets(drug: Optional[dict]) -> list:
    """İlaç kaydındaki mekanizmalardan hedef gen sembolleri (büyük harf, sıralı, tekrarsız)."""
    out = set()
    for m in (drug or {}).get("mechanisms", []) or []:
        for g in m.get("genes", []) or []:
            if g:
                out.add(str(g).upper())
    return sorted(out)


def _best(symbols: Iterable[str], table: Dict[str, dict], types) -> float:
    best = 0.0
    for s in symbols:
        row = table.get(s) or {}
        for t in types:
            v = row.get(t)
            if v is not None and v > best:
                best = float(v)
    return best


def build(
    ranked: pd.DataFrame,
    a_records,
    c_records=(),
    drugs: Optional[dict] = None,
    drug_map: Optional[dict] = None,
    external: Optional[dict] = None,
    universe: Optional[Iterable[str]] = None,
    reference_year: Optional[int] = None,
) -> pd.DataFrame:
    """İlaç anahtarı (ilaç sözlüğü varsa ChEMBL kimliği, yoksa kimyasal kimliği) başına özellik tablosu.

    universe verilirse tablo tam olarak bu anahtarları içerir (literatürde puanı olmayan ilaçlar da, 0 değerle);
    verilmezse sıralamadaki ilaçlar ile genetik/ağ kanıtı olan ilaçların birleşimi alınır.
    external: {"genetic": {SEMBOL: {veri_türü: skor}}, "network": {SEMBOL: z}} — yoksa ilgili kanallar 0 kalır.
      Komşu hastalık kanalı için çalışma zamanında eklenen anahtarlar (Corpus doldurur, JSON'a yazılmaz):
      "atlas" (trial_atlas.TrialAtlas), "atlas_own" (hastalıkta ilaçların ilk çalışma yılı), "atlas_codes" (hastalığın
      MeSH kodları), "atlas_canonical" (tuz → ana molekül). Puan reference_year (geriye dönük testte kesit) itibarıyla.
    """
    drugs = drugs or {}
    drug_map = drug_map or {}
    external = external or {}
    use_dict = bool(drugs)
    genetic_table = {str(k).upper(): v for k, v in (external.get("genetic") or {}).items()}
    network_table = {str(k).upper(): float(v) for k, v in (external.get("network") or {}).items()}

    def key_of(cid: str) -> str:
        return drug_map.get(cid, cid) if use_dict else cid

    if reference_year is None:
        years = [r.year for r in a_records if r.year]
        reference_year = max(years) if years else 0
    similar: Dict[str, float] = {}
    atlas = external.get("atlas")
    if atlas is not None and use_dict:
        similar, _ = atlas.neighbor_scores(external.get("atlas_own") or {}, external.get("atlas_codes") or [],
                                           cutoff=reference_year or None, canonical=external.get("atlas_canonical"))

    direct: Dict[str, int] = defaultdict(int)
    recent: Dict[str, int] = defaultdict(int)
    for rec in a_records:
        keys = {key_of(cid) for cid in rec.chemicals}
        for k in keys:
            direct[k] += 1
            if rec.year and rec.year > reference_year - RECENT_YEARS:
                recent[k] += 1

    c_unique = {r.pmid: r for r in c_records}
    n_c = len(c_unique) or 1
    # popülerlik: geriye dönük testteki sıklık taban çizgisiyle aynı sayım (her makale × her kimyasal anılma)
    freq: Dict[str, int] = defaultdict(int)
    for rec in list(a_records) + list(c_unique.values()):
        for cid in rec.chemicals:
            freq[key_of(cid)] += 1
    in_c: Dict[str, int] = defaultdict(int)
    for rec in c_unique.values():
        for k in {key_of(cid) for cid in rec.chemicals}:
            in_c[k] += 1

    base: Dict[str, dict] = {}
    if ranked is not None and len(ranked):
        for row in ranked.itertuples(index=False):
            k = key_of(row.chemical_id)
            cur = base.get(k)
            if cur is None or float(row.score) > cur["base"]:
                base[k] = {"base": float(row.score), "lit": float(row.lit_score), "mech": float(row.mech_score),
                           "penalty": float(getattr(row, "penalty", 1.0) or 1.0),
                           "ltc": float(getattr(row, "n_bridges", 0) or 0)}

    if universe is None:
        keys = set(base)
        if use_dict and (genetic_table or network_table):
            for k, d in drugs.items():
                t = drug_targets(d)
                if any(s in genetic_table or s in network_table for s in t):
                    keys.add(k)
        universe = sorted(keys)
    else:
        universe = list(universe)

    rows = []
    for k in universe:
        b = base.get(k, {})
        targets = drug_targets(drugs.get(k)) if use_dict else []
        net = max((network_table[s] for s in targets if s in network_table), default=0.0)
        rows.append({
            "key": k,
            "base": b.get("base", 0.0),
            "lit": b.get("lit", 0.0),
            "mech": b.get("mech", 0.0),
            "penalty": b.get("penalty", 1.0),
            "ltc": b.get("ltc", 0.0),
            "direct": math.log1p(direct.get(k, 0)),
            "recent": math.log1p(recent.get(k, 0)),
            "accel": _accel(recent.get(k, 0), direct.get(k, 0)),
            "genetic": _best(targets, genetic_table, GENETIC_TYPES),
            "biology": _best(targets, genetic_table, BIOLOGY_TYPES),
            "network": max(net, 0.0),
            "similar": similar.get(k, 0.0),
            "prevalence": in_c.get(k, 0) / n_c,
            "frequency": float(freq.get(k, 0)),
            "n_targets": len(targets),
        })
    df = pd.DataFrame(rows, columns=["key", "base", "lit", "mech", "penalty", "ltc", "direct", "recent", "accel", "genetic",
                                     "biology", "network", "similar", "prevalence", "frequency", "n_targets"])
    return df.set_index("key")


def _accel(n_recent: int, n_total: int) -> float:
    """Kanıt ivmesi: (1 + yeni) / √(1 + eski). Yeni anılma yoksa 0 (sıralama kanalında 'kanıt yok')."""
    if n_recent <= 0:
        return 0.0
    old = max(0, n_total - n_recent)
    return round((1.0 + n_recent) / math.sqrt(1.0 + old), 6)


def available_channels(features: pd.DataFrame) -> list:
    """Bu tabloda gerçekten bilgi taşıyan kanallar (en az bir ilaçta sıfırdan büyük)."""
    return [c for c in CHANNELS if c in features.columns and (features[c] > 0).any()]


def annotate_external(ranked: pd.DataFrame, drugs: Optional[dict], drug_map: Optional[dict], external: Optional[dict]) -> pd.DataFrame:
    """Her adaya bağımsız kanıtın hangi hedeften geldiğini yazar: genetic_target ("IL23R 0.81"), network_target ("JAK1 z=3.2").

    Puanı değiştirmez; arayüzdeki "Bağımsız kanıt" kutusu ve rapor için açıklamadır.
    """
    out = ranked.copy()
    drugs, drug_map, external = drugs or {}, drug_map or {}, external or {}
    gen = {str(k).upper(): v for k, v in (external.get("genetic") or {}).items()}
    net = {str(k).upper(): float(v) for k, v in (external.get("network") or {}).items()}
    g_col, n_col = [], []
    for cid in out["chemical_id"] if len(out) else []:
        targets = drug_targets(drugs.get(drug_map.get(cid, cid)))
        best_g = max(((max((sc.get(t, 0) for t in GENETIC_TYPES), default=0), s) for s in targets for sc in [gen.get(s, {})]),
                     default=(0, ""))
        best_n = max(((net[s], s) for s in targets if s in net), default=(0.0, ""))
        g_col.append(f"{best_g[1]} {best_g[0]:.2f}" if best_g[0] > 0 else "")
        n_col.append(f"{best_n[1]} z={best_n[0]:.1f}" if best_n[0] > 0 else "")
    out["genetic_target"] = g_col
    out["network_target"] = n_col
    out["similar_from"] = _neighbor_reasons(out, drug_map, external)
    out["stopped_trials"] = _stopped_trials(out, drug_map, external)
    return out


STOP_TOP = 5


def _stopped_trials(ranked: pd.DataFrame, drug_map: dict, external: dict) -> list:
    """Uyarı (puanı değiştirmez): ilacın etkisizlik ya da güvenlik nedeniyle erken sonlandırılan çalışmaları; yalnız bu
    hastalığın kendisi ve en benzer hastalıklarda (komşu kanalının ilk 10 komşusu). Alakasız hastalıklardaki durdurmalar
    gösterilmez: ilk 50 adayın %57'sinde bir kayıt çıkıyordu (çoğu kanser ya da çocuk çalışmaları), uyarı anlamını
    yitiriyordu; benzer hastalıkla sınırlayınca ~%9. Bu hastalık önce, sonra benzerlik, sonra yeniden eskiye; en fazla
    STOP_TOP kayıt, JSON metni.
    Ölçüm: bu bilgi "5 yılda klinik çalışmaya girer mi" sıralamasını iyileştirmedi (başarısız olan ilaçlar çok denenen,
    etkin araştırılan ilaçlardır); bu yüzden kanal değil, okuyucuya uyarıdır."""
    atlas = external.get("atlas")
    if atlas is None or not len(ranked) or not getattr(atlas, "failed", None):
        return [""] * len(ranked)
    _, used = atlas.neighbor_scores(external.get("atlas_own") or {}, external.get("atlas_codes") or [], cutoff=None,
                                    canonical=external.get("atlas_canonical"))
    sim = {u["mesh"]: u["similarity"] for u in used}
    own = {str(c).split(":")[-1].upper() for c in external.get("atlas_codes") or [] if c}
    canonical = external.get("atlas_canonical") or {}
    col = []
    for cid in ranked["chemical_id"]:
        k = drug_map.get(cid, cid)
        k = canonical.get(k, k)
        rows = [r for r in atlas.stopped(k) if r[1].upper() in own or r[1] in sim]
        if not rows:
            col.append("")
            continue
        rows = sorted(rows, key=lambda r: (r[1].upper() not in own, -sim.get(r[1], 0.0), -r[2]))[:STOP_TOP]
        col.append(json.dumps([{"tur": kind, "mesh": code, "hastalik": atlas.names.get(code, code), "yil": y, "nct": nct,
                                "ayni": code.upper() in own, "benzerlik": sim.get(code)} for kind, code, y, nct in rows],
                              ensure_ascii=False, separators=(",", ":")))
    return col


def _neighbor_reasons(ranked: pd.DataFrame, drug_map: dict, external: dict, top: int = 3) -> list:
    """Komşu hastalık kanıtının gerekçesi: ilacın denendiği en benzer hastalıklar ("Uveitis 0.22; Retinal Vasculitis 0.33")."""
    atlas = external.get("atlas")
    if atlas is None or not len(ranked):
        return [""] * len(ranked)
    _, used = atlas.neighbor_scores(external.get("atlas_own") or {}, external.get("atlas_codes") or [], cutoff=None,
                                    canonical=external.get("atlas_canonical"))
    sets = atlas.tried_before(_this_year())
    canonical = external.get("atlas_canonical") or {}
    col = []
    for cid in ranked["chemical_id"]:
        k = drug_map.get(cid, cid)
        k = canonical.get(k, k)
        hits = [f"{u['name'] or u['mesh']} {u['similarity']:.2f}" for u in used if k in sets.get(u["mesh"], set())]
        col.append("; ".join(hits[:top]))
    return col


def _this_year() -> int:
    return datetime.date.today().year
