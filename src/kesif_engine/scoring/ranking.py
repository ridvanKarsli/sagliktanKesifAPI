"""Swanson ABC mantığıyla, ilaç sözlüğü ve yön bilgisiyle güçlendirilmiş aday sıralaması.

A = hastalık, B = hastalık makalelerinde sık geçen genler, C = ilaçlar.

Puan bileşenleri (her biri çıktıda ayrı sütun):
  lit_score  : literatür köprü puanı, her (gen, ilaç) bağı için
               log(1 + n_AB) * log(lift) * log(1 + n_BC)
  mech_score : ChEMBL mekanizma puanı; ilacın bilinen hedefi köprü genlerden biriyse
               w_mech * log(1 + n_AB)
  score      : lit_score + mech_score

Yön (direction): PubTator ilişkileri ve ChEMBL etki tipinden.
  gen-hastalık: A literatüründe Positive/Negative_Correlation -> gen hastalıkta "artmış"/"azalmış"
  ilaç-gen    : C literatüründe Negative_Correlation ya da ChEMBL INHIBITOR vb. -> "inhibe"
  tutarlı     : (artmış & inhibe) ya da (azalmış & aktive)
  ters        : (artmış & aktive) ya da (azalmış & inhibe)
Köprü gen özgüllüğü: genin hastalık literatüründeki payı / tüm literatürdeki payı >= min_spec olmalı; yoksa
  köprü olamaz (hipertansiyonda TNF-alpha, RP'de insülin gibi genel genler elenir).
Güvenlik cezası: ilaç-hastalık ilişkileri 'tetikleme' yönündeyse puan harm_penalty ile çarpılır (DOCA, L-NAME,
  kalp yetersizliğinde anti-TNF, hipertansiyonda bevacizumab).
İlaç sınıfı (drugspace): endojen moleküller ve tanı ajanları aday değildir; besin/takviye etiketlenir.
Çıktı tedavi önerisi değil, kaynaklı hipotez listesidir.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from kesif_engine.core import config
from kesif_engine.scoring import drugspace

COLUMNS = [
    "chemical_id",
    "chemical",
    "chembl_id",
    "max_phase",
    "kind",
    "status",
    "score",
    "lit_score",
    "mech_score",
    "penalty",
    "direction",
    "harm_signal",
    "n_treat_rel",
    "n_harm_rel",
    "n_bridges",
    "mechanism",
    "n_direct_a",
    "direct_pmids",
    "top_bridges",
    "aliases",
]

POS = "Positive_Correlation"
NEG = "Negative_Correlation"


@dataclass
class Rec:
    pmid: str
    year: Optional[int]
    genes: dict  # gen_id -> etiket
    chemicals: dict  # kimyasal_id -> etiket
    relations: list = field(default_factory=list)  # [tip, tip1, kimlik1, tip2, kimlik2]
    diseases: dict = field(default_factory=dict)  # hastalık_id -> etiket
    text: str = ""  # başlık + özet (biyolojik ilaç taraması için)


@dataclass
class Params:
    top_k: int = 30            # doğrudan sıklıkla seçilen köprü gen sayısı
    expand_bridges: bool = True  # gen-gen ilişkileriyle yolak komşularını ekle
    max_expand: int = 40       # eklenecek en fazla komşu gen
    min_relations: int = 2     # komşu sayılmak için birincil genlerle en az ilişki
    min_articles: int = 3
    min_links: int = 1
    min_bc: int = 2
    min_lift: float = 2.0
    w_mech: float = 1.0
    propagate_direction: bool = True  # yönü bilinmeyen genlerin yönünü komşulardan türet
    ctx_floor: float = 0.25  # bağlam uyumu çarpanının tabanı (1.0 = kapalı); Behçet'te 2 kesitte doğrulandı
    min_spec: float = 1.5    # köprü gen özgüllüğü eşiği (hastalıktaki pay / literatürdeki pay); 0 = kapalı. 2.0 astımda IL-8'i (1.94), Behçet'te IL-2'yi (1.98) eliyordu
    harm_penalty: float = 0.3  # 'tetikleme sinyali' olan ilaçların puan çarpanı (karışık: karekökü); 1 = kapalı
    max_evidence: int = 5

    FIELDS = ("top_k", "expand_bridges", "max_expand", "min_relations", "min_articles", "min_links", "min_bc", "min_lift", "w_mech", "propagate_direction", "ctx_floor", "min_spec", "harm_penalty")

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.FIELDS}

    @classmethod
    def from_dict(cls, d: dict) -> Params:
        p = cls()
        for k in cls.FIELDS:
            if k in d and d[k] is not None:
                cur = getattr(p, k)
                val = d[k]
                if isinstance(cur, bool):
                    val = val if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes", "on")
                elif isinstance(cur, int):
                    val = int(val)
                elif isinstance(cur, float):
                    val = float(val)
                setattr(p, k, val)
        return p


def is_stop(label: str) -> bool:
    return (label or "").lower().strip() in config.STOP_CHEMICALS


def _is_abbreviation(label: str) -> bool:
    letters = label.replace("-", "").replace(" ", "")
    return 0 < len(letters) <= 6 and letters.upper() == letters


def pick_label(votes: Counter) -> str:
    """En çok kullanılan yazımı seçer; kısaltma (MTX, CZP) varsa açık adı tercih eder."""
    ordered = [label for label, _ in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0])) if label]  # eşitlikte alfabetik
    for label in ordered:
        if not _is_abbreviation(label):
            return label
    return ordered[0] if ordered else ""


def top_b_genes(a_records, top_k: int = 30, min_articles: int = 3) -> list:
    """Hastalık makalelerinde en sık geçen genler: [(gen_id, etiket, makale_sayısı)]."""
    counts: Counter = Counter()
    votes: dict = defaultdict(Counter)
    for rec in a_records:
        for gid, label in rec.genes.items():
            counts[gid] += 1
            votes[gid][label] += 1
    items = []
    for gid, n in counts.most_common():
        if n < min_articles:
            break
        label = votes[gid].most_common(1)[0][0]
        if gid in config.STOP_GENE_IDS or label.lower() in config.STOP_GENE_LABELS:
            continue
        items.append((gid, label, n))
    return items[:top_k]


def select_bridges(a_records, p: Params) -> list:
    """Köprü genleri: sıklığa göre ilk top_k gen + bunlarla gen-gen ilişkisi olan yolak komşuları.

    Komşu genişletme, JAK/STAT gibi hastalık makalelerinde az ama merkezi genlerle ilişkili geçen genleri
    yakalar; hastalığa özel liste gerektirmez. Dönen: [(gen_id, etiket, makale_sayısı, kaynak)]
    kaynak: 'primary' | 'neighbor'
    """
    items = top_b_genes(a_records, top_k=10**9, min_articles=p.min_articles)
    primary = [(g, lab, n, "primary") for g, lab, n in items[: p.top_k]]
    if not p.expand_bridges or p.max_expand <= 0:
        return primary
    pset = {g for g, _, _, _ in primary}
    nb: Counter = Counter()
    for rec in a_records:
        for rtype, t1, i1, t2, i2 in rec.relations:
            if t1 == "Gene" and t2 == "Gene":
                if i1 in pset and i2 not in pset:
                    nb[i2] += 1
                elif i2 in pset and i1 not in pset:
                    nb[i1] += 1
    rest = [(g, lab, n) for g, lab, n in items[p.top_k :] if nb[g] >= p.min_relations]
    rest.sort(key=lambda it: (-nb[it[0]], -it[2]))
    return primary + [(g, lab, n, "neighbor") for g, lab, n in rest[: p.max_expand]]


def specificity(n_ab: int, n_a: int, gene_counts: dict, gid: str):
    """Genin hastalık literatüründeki payı / tüm literatürdeki payı. Sayı yoksa None (eşik uygulanmaz)."""
    if not gene_counts or n_a <= 0:
        return None
    rec = gene_counts.get(gid)
    total = (gene_counts.get("_total") or {}).get("n")
    if not rec or not total:
        return None
    n_bg = rec.get("n") if isinstance(rec, dict) else rec
    if not n_bg:
        return None
    return (n_ab / n_a) / (n_bg / total)


NEIGHBOR_SPEC_FACTOR = 0.33  # yolak komşuları için eşik: min_spec × 0.33


def filter_specific(bridges, n_a: int, gene_counts: dict, min_spec: float):
    """Özgüllüğü eşiğin altındaki köprüleri eler. Dönen: (kalanlar, elenenler[(gid, label, spec)]).

    Doğrudan (sıklıkla seçilen) köprüler tam eşiğe tabidir. Yolak komşuları (JAK1, STAT3, NLRP3 gibi, hastalığa
    özgü genlerle gen-gen ilişkisi üzerinden seçilenler) doğası gereği genel genlerdir; onlara eşiğin üçte biri
    uygulanır — yalnızca hastalık literatüründe belirgin biçimde SEYREK olanlar (IL-6 hipertansiyonda 0,35) elenir.
    Behçet'te tam eşik JAK1'i (1,2) eliyordu; JAK inhibitörleri bu köprüyle bulunuyordu.
    """
    if not min_spec or min_spec <= 0 or not gene_counts:
        return list(bridges), []
    keep, dropped = [], []
    for item in bridges:
        gid, label, n = item[0], item[1], item[2]
        source = item[3] if len(item) > 3 else "primary"
        threshold = min_spec if source == "primary" else min_spec * NEIGHBOR_SPEC_FACTOR
        spec = specificity(n, n_a, gene_counts, gid)
        if spec is not None and spec < threshold:
            dropped.append((gid, label, round(spec, 2)))
        else:
            keep.append(item)
    return keep, dropped


def gene_disease_direction(a_records, gene_ids, disease_id: Optional[str] = None) -> dict:
    """Her köprü gen için A literatüründeki gen-hastalık ilişkilerinden yön: 'up' / 'down' / 'mutated' / 'unknown'."""
    counts: dict = defaultdict(Counter)
    wanted = set(gene_ids)
    for rec in a_records:
        for rtype, t1, i1, t2, i2 in rec.relations:
            if rtype not in (POS, NEG, "Association"):
                continue
            for gid, other_t, other_i in ((i1, t2, i2), (i2, t1, i1)):
                if gid in wanted and (other_t == "Disease" or other_i.startswith("MESH:")):
                    if disease_id and other_i != disease_id:
                        continue
                    counts[gid][rtype] += 1
    out = {}
    for gid in gene_ids:
        c = counts[gid]
        assoc, corr = c["Association"], c[POS] + c[NEG]
        if (assoc >= 5 and corr < 0.2 * assoc) or (assoc >= 2 and corr <= 1):
            # Genetik hastalıklarda (MEFV-FMF, PDE6A/PDE6B-RP, USH2A-RP) ilişki 'mutasyon' türündendir; artış/azalış
            # değil. Tek tük korelasyon raporu bunu değiştirmez. Bir inhibitörün 'tutarlı' sayılması yanlış olur
            # (kayıp-fonksiyon mutasyonunda zararlı olabilir). Bu genlere komşulardan yön de türetilmez
            # (RP'de PDE6A'ya 'artmış' atanıp dipyridamole'ün 'tutarlı' çıkması bu yüzdendi).
            out[gid] = "mutated"
        elif c[POS] >= 2 * c[NEG] and c[POS] >= 1:
            out[gid] = "up"
        elif c[NEG] >= 2 * c[POS] and c[NEG] >= 1:
            out[gid] = "down"
        else:
            out[gid] = "unknown"
    return out


def known_therapies(a_records, disease_id: Optional[str], drugs: Optional[dict] = None, drug_map: Optional[dict] = None,
                    min_rel: int = 2, therapy_pmids=None, min_therapy: int = 3) -> pd.DataFrame:
    """Hastalık literatüründe doğrudan tedavi ilişkisi raporlanan ilaçlar: 'zaten çalışılanlar' haritası.

    İki kaynak: (1) PubTator ilaç-hastalık ilişkileri (Negative_Correlation = tedavi yönü), (2) PubMed
    "drug therapy" alt başlıklı makalelerde anılma sayısı. İkincisi, PubTator'ın etiketlemediği biyolojikleri
    (anakinra, etanercept) de kapsar. Aday listesinden farklıdır: burada ilaç hastalıkla doğrudan anılır.
    """
    drugs = drugs or {}
    drug_map = drug_map or {}
    therapy_pmids = set(therapy_pmids or [])
    rel: dict = defaultdict(Counter)
    years: dict = defaultdict(list)
    arts: dict = defaultdict(set)
    therapy_arts: dict = defaultdict(set)
    votes: dict = defaultdict(Counter)
    for rec in a_records:
        for cid, lab in rec.chemicals.items():
            key = drug_map.get(cid, cid) if drugs else cid
            votes[key][lab.lower()] += 1
            if rec.pmid in therapy_pmids:
                therapy_arts[key].add(rec.pmid)
                if rec.year:
                    years[key].append(rec.year)
        for rtype, t1, i1, t2, i2 in rec.relations:
            if rtype not in (POS, NEG) or {t1, t2} != {"Chemical", "Disease"}:
                continue
            cid, did = (i1, i2) if t1 == "Chemical" else (i2, i1)
            if disease_id and did != disease_id:
                continue
            key = drug_map.get(cid, cid) if drugs else cid
            rel[key][rtype] += 1
            arts[key].add(rec.pmid)
            if rec.year:
                years[key].append(rec.year)
    gene_names = drugspace.gene_product_names(a_records)
    rows = []
    for key in sorted(set(rel) | set(therapy_arts)):  # sıralı: çıktı her çalıştırmada aynı
        c = rel[key]
        if drugs and key not in drugs:
            continue
        if c[NEG] + c[POS] < min_rel and len(therapy_arts[key]) < min_therapy:
            continue
        label = (drugs.get(key, {}).get("name") or pick_label(votes[key]) or key).lower()
        if is_stop(label):
            continue
        kind = drugspace.classify(drugs.get(key, {}), pick_label(votes[key]), gene_names)
        if kind == drugspace.KIND_DIAGNOSTIC:
            continue
        rows.append(
            {
                "chembl_id": key if drugs else "",
                "chemical": label,
                "max_phase": drugs.get(key, {}).get("max_phase", ""),
                "kind": kind,
                "n_treat_rel": c[NEG],
                "n_harm_rel": c[POS],
                "n_therapy_articles": len(therapy_arts[key]),
                "harm_signal": harm_label(c),
                "n_articles": len(arts[key] | therapy_arts[key]),
                "first_year": min(years[key]) if years[key] else None,
                "last_year": max(years[key]) if years[key] else None,
            }
        )
    cols = ["chembl_id", "chemical", "max_phase", "kind", "n_treat_rel", "n_harm_rel", "n_therapy_articles", "harm_signal", "n_articles", "first_year", "last_year"]
    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows, columns=cols)
    out["_s"] = out["n_treat_rel"] + out["n_therapy_articles"]
    out = out.sort_values(["_s", "n_articles", "chemical"], ascending=[False, False, True], kind="mergesort")
    out = out.drop(columns="_s").reset_index(drop=True)
    out.index = out.index + 1
    out.index.name = "rank"
    return out


def propagate_direction(a_records, direction: dict, min_support: int = 2) -> dict:
    """Yönü bilinmeyen genlerin yönünü, yönü bilinen genlerle gen-gen ilişkilerinden türetir.

    Pozitif korelasyon/ilişki aynı yönü, negatif korelasyon ters yönü taşır. En az min_support ilişki ve
    açık çoğunluk gerekir. Dönen: {gid: "relations" | "propagated" | "unknown"} (kaynak); direction yerinde güncellenir.
    """
    source = {g: ("unknown" if d == "unknown" else "relations") for g, d in direction.items()}
    unknown = {g for g, d in direction.items() if d == "unknown"}
    known_dirs = {"up", "down"}
    if not unknown:
        return source
    votes: dict = defaultdict(Counter)
    for rec in a_records:
        for rtype, t1, i1, t2, i2 in rec.relations:
            if t1 != "Gene" or t2 != "Gene" or rtype not in (POS, NEG, "Association"):
                continue
            for a, b in ((i1, i2), (i2, i1)):
                if a in unknown and direction.get(b, "unknown") in known_dirs:
                    d = direction[b]
                    if rtype == NEG:
                        d = "down" if d == "up" else "up"
                    votes[a][d] += 1
    for g in unknown:
        v = votes[g]
        top = v["up"] if v["up"] >= v["down"] else v["down"]
        other = v["down"] if v["up"] >= v["down"] else v["up"]
        if top >= min_support and top >= 2 * other:
            direction[g] = "up" if v["up"] >= v["down"] else "down"
            source[g] = "propagated"
    return source


def _drug_gene_relations(records, cid: str, gid: str) -> Counter:
    c: Counter = Counter()
    for rec in records:
        for rtype, _, i1, _, i2 in rec.relations:
            if {i1, i2} == {cid, gid}:
                c[rtype] += 1
    return c


def _effect_from_relations(c: Counter) -> str:
    if c[NEG] >= 1 and c[NEG] >= 2 * c[POS]:
        return "inhibit"
    if c[POS] >= 1 and c[POS] >= 2 * c[NEG]:
        return "activate"
    return "unknown"


def _effect_from_action(action: str) -> str:
    if action in config.INHIBITING_ACTIONS:
        return "inhibit"
    if action in config.ACTIVATING_ACTIONS:
        return "activate"
    return "unknown"


def _consistency(gene_dir: str, effect: str, hereditary: bool = False) -> str:
    """'mutated' (genetik ilişki) yön bilgisi taşımaz: kayıp-fonksiyon (PDE6B-RP) ile kazanç-fonksiyon (MEFV-FMF)
    ayırt edilemez, bu yüzden tutarlılık 'bilinmiyor' kalır ve mekanizma bonusu yarıya iner (FMF'de IL-1/TNF
    inhibitörlerinin 'ters' çıkmasına yol açtığı için kalıtsal hastalıkta 'ters' kuralı kaldırıldı)."""
    if gene_dir in ("unknown", "mutated") or effect == "unknown":
        return "unknown"
    if (gene_dir == "up" and effect == "inhibit") or (gene_dir == "down" and effect == "activate"):
        return "consistent"
    return "opposite"


def drug_disease_relations(a_records, disease_id: Optional[str]) -> dict:
    """Her kimyasal için hastalıkla ilişkisi: {cid: Counter(Negative_Correlation=tedavi yönü, Positive_Correlation=tetikleme yönü)}."""
    out: dict = defaultdict(Counter)
    for rec in a_records:
        for rtype, t1, i1, t2, i2 in rec.relations:
            if rtype not in (POS, NEG) or {t1, t2} != {"Chemical", "Disease"}:
                continue
            cid, did = (i1, i2) if t1 == "Chemical" else (i2, i1)
            if disease_id and did != disease_id:
                continue
            out[cid][rtype] += 1
    return out


def harm_label(rel: Counter) -> str:
    """İlaç-hastalık ilişki raporlarından etiket.

    Negative_Correlation = ilaç hastalığı azaltıyor (tedavi yönü); Positive_Correlation = ilaç hastalığı
    tetikliyor/alevlendiriyor. Oran temelli: 405 tedavi raporuna karşı 2 tetikleme raporu 'tedavi yönü',
    15'e karşı 3 'karışık', 0'a karşı 2 'tetikleme sinyali'.
    """
    pos, neg = rel[POS], rel[NEG]
    if pos == 0 and neg == 0:
        return "yok"
    if pos >= 2 and pos >= neg:
        return "tetikleme sinyali"
    if pos >= 2 and pos >= 0.15 * neg:
        return "karışık"
    if pos == 1 and neg == 0:
        return "tek tetikleme raporu"
    return "tedavi yönü"


def context_set(a_records, c_records, disease_id: Optional[str], min_enrich: float = 3.0, min_count: int = 5) -> set:
    """Hastalık literatüründe, gen literatürüne göre zenginleşmiş eş hastalıklar (üveit, vaskülit, oral ülser...).

    'İltihap', 'enfeksiyon' gibi her alanda geçen genel hastalıklar zenginleşmediği için dışarıda kalır; böylece
    kanser bağlamından gelen gen-ilaç makaleleri hastalık bağlamında sayılmaz.
    """
    a_counts: Counter = Counter()
    for rec in a_records:
        for did in rec.diseases:
            a_counts[did] += 1
    c_counts: Counter = Counter()
    for rec in c_records:
        for did in rec.diseases:
            c_counts[did] += 1
    n_a, n_c = max(len(a_records), 1), max(len(c_records), 1)
    s = set()
    for did, n in a_counts.items():
        if n < min_count:
            continue
        enrich = (n / n_a) / ((c_counts[did] + 1) / n_c)
        if enrich >= min_enrich:
            s.add(did)
    if disease_id:
        s.add(disease_id)
    return s


def summarize_direction(flags) -> str:
    flags = set(flags)
    if "consistent" in flags and "opposite" in flags:
        return "karışık"
    if "consistent" in flags:
        return "tutarlı"
    if "opposite" in flags:
        return "ters"
    return "bilinmiyor"


def rank_candidates(
    a_records,
    c_by_gene: dict,
    drugs: Optional[dict] = None,
    drug_map: Optional[dict] = None,
    gene_info: Optional[dict] = None,
    disease_id: Optional[str] = None,
    params: Optional[Params] = None,
    gene_counts: Optional[dict] = None,
    therapy_pmids=None,
    hereditary: bool = False,
    **overrides,
):
    """Aday ilaçları puanlar. (DataFrame, kanıt sözlüğü) döndürür.

    drugs: chembl_id -> ilaç kaydı (ChEMBL sözlüğü). drug_map: chem_id -> chembl_id.
    İkisi de verildiğinde yalnızca ilaç sözlüğüne eşlenen kimyasallar aday olur.
    gene_counts: gen arka plan sayıları (genes.fetch_gene_counts); köprü özgüllüğü için.
    therapy_pmids: tedavi odaklı makale kimlikleri; 'tedavi olarak çalışılmış' durumunu belirlemek için.
    hereditary: hastalık kalıtsal (MeSH C16) ise mutasyonlu gen inhibitörleri 'ters' sayılır.
    """
    p = params or Params()
    for k, v in overrides.items():
        setattr(p, k, v)
    drugs = drugs or {}
    drug_map = drug_map or {}
    gene_info = gene_info or {}
    therapy_pmids = set(therapy_pmids or [])
    use_dict = bool(drugs)

    b_all = select_bridges(a_records, p)
    b_all, dropped = filter_specific(b_all, len(a_records), gene_counts or {}, p.min_spec)
    b_items = [(g, lab, n) for g, lab, n, _ in b_all]
    if c_by_gene:
        # yalnızca literatürü indirilmiş köprüler puanlanabilir (fetch sonrası eklenen komşular atlanır)
        b_items = [it for it in b_items if it[0] in c_by_gene]
    b_ids = [g for g, _, _ in b_items]
    gene_dir = gene_disease_direction(a_records, b_ids, disease_id)
    gene_dir_src = propagate_direction(a_records, gene_dir) if p.propagate_direction else {g: "relations" for g in b_ids}
    symbol_to_gid: dict = {}
    for gid, label, _ in b_items:
        sym = (gene_info.get(gid) or {}).get("symbol") or ""
        for name in (sym, label):
            if name:
                symbol_to_gid.setdefault(name.upper(), gid)

    def species_of(gid: str) -> str:
        tax = (gene_info.get(gid) or {}).get("taxid")
        names = {9606: "insan", 10090: "fare", 10116: "sıçan"}
        return names.get(int(tax), "diğer tür") if tax else ""

    ab_pmids: dict = defaultdict(list)
    direct: dict = defaultdict(list)
    therapy_direct: dict = defaultdict(set)
    votes: dict = defaultdict(Counter)
    for rec in a_records:
        for gid in rec.genes:
            ab_pmids[gid].append(rec.pmid)
        for cid, label in rec.chemicals.items():
            direct[cid].append(rec.pmid)
            votes[cid][label.lower()] += 1
            if rec.pmid in therapy_pmids:
                therapy_direct[cid].add(rec.pmid)

    c_unique: dict = {}
    for gid in b_ids:
        for rec in c_by_gene.get(gid, []):
            c_unique[rec.pmid] = rec
    n_c = len(c_unique)
    df: Counter = Counter()
    for rec in c_unique.values():
        for cid, label in rec.chemicals.items():
            df[cid] += 1
            votes[cid][label.lower()] += 1
    chosen = {cid: pick_label(v) for cid, v in votes.items()}
    gene_names = drugspace.gene_product_names(list(a_records) + list(c_unique.values()))

    kind_cache: dict = {}

    def kind_of(cid: str) -> str:
        if cid not in kind_cache:
            drug = drugs.get(drug_map.get(cid, "")) if use_dict else {}
            kind_cache[cid] = drugspace.classify(drug or {}, chosen.get(cid, ""), gene_names)
        return kind_cache[cid]

    def eligible(cid: str) -> bool:
        if is_stop(chosen[cid]):
            return False
        if use_dict and not (cid in drug_map and drug_map[cid] in drugs):
            return False
        return kind_of(cid) != drugspace.KIND_DIAGNOSTIC

    lit: dict = defaultdict(float)
    links: dict = defaultdict(list)
    n_ab_of = {gid: n for gid, _, n in b_items}
    glabel_of = {gid: label for gid, label, _ in b_items}
    spec_of = {gid: specificity(n, len(a_records), gene_counts or {}, gid) for gid, _, n in b_items}
    ctx = context_set(a_records, list(c_unique.values()), disease_id) if p.ctx_floor < 1.0 else set()
    harm = drug_disease_relations(a_records, disease_id)

    for gid, glabel, n_ab in b_items:
        recs = [r for r in c_by_gene.get(gid, []) if gid in r.genes]
        n_b = len(recs)
        if n_b == 0 or n_c == 0:
            continue
        bc: dict = defaultdict(list)
        bc_ctx: Counter = Counter()
        for rec in recs:
            in_ctx = bool(ctx) and bool(set(rec.diseases) & ctx)
            for cid in rec.chemicals:
                bc[cid].append(rec.pmid)
                if in_ctx:
                    bc_ctx[cid] += 1
        for cid, pmids in bc.items():
            if len(pmids) < p.min_bc or not eligible(cid):
                continue
            expected = n_b * df[cid] / n_c
            log_lift = math.log((len(pmids) + 0.5) / (expected + 0.5))
            if log_lift < max(math.log(p.min_lift), 1e-12):
                continue
            # bağlam uyumu: gen-ilaç makaleleri hastalığın eş hastalık bağlamında mı (üveit, vaskülit...) yoksa
            # tamamen başka bir alanda mı (kanser)? taban ctx_floor, tamamı uyumluysa 1.0
            ctx_frac = bc_ctx[cid] / len(pmids) if ctx else 1.0
            ctx_mult = p.ctx_floor + (1.0 - p.ctx_floor) * ctx_frac
            contrib = math.log1p(n_ab) * log_lift * math.log1p(len(pmids)) * ctx_mult
            lit[cid] += contrib
            rel = _drug_gene_relations(recs, cid, gid)
            effect = _effect_from_relations(rel)
            links[cid].append(
                {
                    "gene_id": gid,
                    "gene": glabel,
                    "species": species_of(gid),
                    "source": "literature",
                    "n_ab": n_ab,
                    "n_bc": len(pmids),
                    "lift": round(math.exp(log_lift), 2),
                    "specificity": round(spec_of[gid], 1) if spec_of.get(gid) is not None else None,
                    "context_fraction": round(ctx_frac, 2),
                    "contribution": round(contrib, 4),
                    "gene_direction": gene_dir.get(gid, "unknown"),
                    "gene_direction_source": gene_dir_src.get(gid, "unknown"),
                    "drug_effect": effect,
                    "relations": {POS: rel[POS], NEG: rel[NEG]},
                    "consistency": _consistency(gene_dir.get(gid, "unknown"), effect, hereditary),
                    "ab_pmids": ab_pmids[gid][: p.max_evidence],
                    "bc_pmids": pmids[: p.max_evidence],
                }
            )

    mech: dict = defaultdict(float)
    mech_links: dict = defaultdict(list)
    if use_dict:
        for cid, chembl_id in drug_map.items():
            drug = drugs.get(chembl_id)
            if not drug or cid not in votes or not eligible(cid):
                continue
            for m in drug.get("mechanisms", []):
                hit_gids = sorted({symbol_to_gid[g.upper()] for g in m.get("genes", []) if g.upper() in symbol_to_gid})
                for gid in hit_gids:
                    effect = _effect_from_action(m.get("action_type", ""))
                    gdir = gene_dir.get(gid, "unknown")
                    cons = _consistency(gdir, effect, hereditary)
                    bonus = p.w_mech * math.log1p(n_ab_of[gid])
                    if cons == "opposite":
                        bonus = 0.0  # mekanizma hastalık yönünde: bilgi kanıt panelinde kalır, puan almaz
                    elif gdir == "mutated" or gene_dir_src.get(gid) == "propagated":
                        bonus *= 0.5  # yön belirsiz/türetilmiş: mekanizma bonusu yarım
                    mech[cid] += bonus
                    mech_links[cid].append(
                        {
                            "gene_id": gid,
                            "gene": glabel_of[gid],
                            "species": species_of(gid),
                            "source": "chembl",
                            "n_ab": n_ab_of[gid],
                            "specificity": round(spec_of[gid], 1) if spec_of.get(gid) is not None else None,
                            "action_type": m.get("action_type", ""),
                            "moa": m.get("moa", ""),
                            "contribution": round(bonus, 4),
                            "gene_direction": gdir,
                            "gene_direction_source": gene_dir_src.get(gid, "unknown"),
                            "drug_effect": effect,
                            "consistency": cons,
                            "ab_pmids": ab_pmids[gid][: p.max_evidence],
                        }
                    )

    # Aynı ilacın birden fazla kimliğini (MeSH D/C kimliği, metin taraması CHEMBL kimliği) tek satırda birleştir.
    groups: dict = defaultdict(list)
    for cid in sorted(set(lit) | set(mech)):  # sıralı: gruplama ve eşitlik çözümü her çalıştırmada aynı
        key = drug_map.get(cid, cid) if use_dict else cid
        groups[key].append(cid)

    rows = []
    evidence: dict = {}
    for key, cids in groups.items():
        lit_links = sorted((l for c in cids for l in links.get(c, [])), key=lambda x: -x["contribution"])
        m_links = [l for c in cids for l in mech_links.get(c, [])]
        seen_m: set = set()
        unique_m = []
        for l in m_links:
            if l["gene_id"] not in seen_m:
                seen_m.add(l["gene_id"])
                unique_m.append(l)
        m_links = unique_m
        if len(lit_links) < p.min_links and not m_links:
            continue
        # Ana kimlik: en yüksek puanlı; eşitlikte makale işaretlemesinden gelen kimlik (MeSH) metin taramasına (CHEMBL:) tercih edilir
        main = max(cids, key=lambda c: (round(lit[c] + mech[c], 9), not c.startswith("CHEMBL:")))
        all_links = lit_links + m_links
        evidence[main] = all_links
        drug = drugs.get(key, {}) if use_dict else {}
        votes_all: Counter = Counter()
        for c in cids:
            votes_all.update(votes[c])
        label = drug.get("name", "").lower() or pick_label(votes_all)
        ranked_labels = sorted(votes_all.items(), key=lambda kv: (-kv[1], kv[0]))  # sıklık, eşitlikte alfabetik
        aliases = [lab for lab, _ in ranked_labels[:5] if lab != label][:3]
        mech_desc = "; ".join(f"{l['action_type'].lower()} {l['gene']}" for l in m_links[:3])
        direct_pmids = sorted({pm for c in cids for pm in direct.get(c, [])})
        n_therapy = len({pm for c in cids for pm in therapy_direct.get(c, set())})
        harm_rel: Counter = Counter()
        for c in cids:
            harm_rel.update(harm.get(c, {}))
        harm_sig = harm_label(harm_rel)
        penalty = 1.0
        if p.harm_penalty < 1.0:
            if harm_sig == "tetikleme sinyali":
                penalty = p.harm_penalty
            elif harm_sig == "karışık":
                penalty = math.sqrt(p.harm_penalty)
        lit_total = sum(lit[c] for c in cids)
        mech_total = max((mech[c] for c in cids), default=0.0)
        kind = drugspace.classify(drug, label, gene_names) if use_dict else kind_of(main)
        if harm_rel[NEG] >= 2 or n_therapy >= 3:
            status = "tedavi olarak çalışılmış"
        elif direct_pmids:
            status = "literatürde anılmış"
        else:
            status = "yeni"
        rows.append(
            {
                "chemical_id": main,
                "chemical": label,
                "chembl_id": drug.get("chembl_id", ""),
                "max_phase": drug.get("max_phase", ""),
                "kind": kind,
                "status": status,
                "score": round((lit_total + mech_total) * penalty, 4),
                "lit_score": round(lit_total, 4),
                "mech_score": round(mech_total, 4),
                "penalty": round(penalty, 2),
                "direction": summarize_direction(l["consistency"] for l in all_links),
                "harm_signal": harm_sig,
                "n_treat_rel": harm_rel[NEG],
                "n_harm_rel": harm_rel[POS],
                "n_bridges": len(lit_links),
                "mechanism": mech_desc,
                "n_direct_a": len(direct_pmids),
                "direct_pmids": ";".join(direct_pmids[: p.max_evidence]),
                "top_bridges": " | ".join(
                    f"{l['gene']}(A:{l['n_ab']},C:{l['n_bc']},lift:{l['lift']})" for l in lit_links[:3]
                ),
                "aliases": ";".join(aliases),
            }
        )

    if not rows:
        out = pd.DataFrame(columns=COLUMNS)
    else:
        # Eşit puanda kararlı sıra: literatür puanı, köprü sayısı, sonra ad ve kimlik (aynı veri → aynı sıralama)
        out = pd.DataFrame(rows, columns=COLUMNS).sort_values(
            ["score", "lit_score", "n_bridges", "chemical", "chemical_id"], ascending=[False, False, False, True, True], kind="mergesort"
        )
        out = out.reset_index(drop=True)
        out.index = out.index + 1
        out.index.name = "rank"
    out.attrs["dropped_bridges"] = dropped
    return out, evidence
