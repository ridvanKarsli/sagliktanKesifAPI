"""MeSH hastalık kataloğu: NLM'nin ASCII MeSH dosyasından hastalık dalındaki tüm tanımlayıcılar ve eş adları.

Kaynak: https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/asciimesh/d<yıl>.bin  (ücretsiz, ~35 MB)
Hastalık dalları: C (Diseases), F03 (Mental Disorders).
Çıktı: data/mesh/diseases.json  -> {"year":..., "n":..., "items":[{ui, term, entries, trees}]}

Arama aksan ve büyük/küçük harf duyarsızdır: "behçet", "behcet", "Behçet hastalığı" aynı kaydı bulur.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import unicodedata
from pathlib import Path

from kesif_engine.core import config
from kesif_engine.core.net import HttpClient

log = logging.getLogger(__name__)
BASE = "https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/asciimesh/d{year}.bin"
DISEASE_TREES = ("C", "F03")
_NONWORD = re.compile(r"[^a-z0-9]+")

# Türkçe'de sık kullanılan birkaç ek ve genel sözcük; aramada atılır
_STOP = {"hastaligi", "hastalik", "sendromu", "sendrom", "disease", "syndrome", "disorder", "the", "of", "and"}


def catalog_path() -> Path:
    path = config.DATA_DIR / "mesh" / "diseases.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return _NONWORD.sub(" ", text).strip()


def tokens(text: str) -> list:
    return [t for t in normalize(text).split() if len(t) >= 3 and t not in _STOP]


def parse_ascii(text: str) -> list:
    """ASCII MeSH kayıtlarını ayrıştırır; yalnızca hastalık dalındakileri döndürür."""
    items = []
    rec: dict = {}

    def flush():
        if rec.get("ui") and rec.get("term") and any(t.startswith(DISEASE_TREES) for t in rec.get("trees", [])):
            entries = []
            for e in rec.get("entries", []):
                if e and e.lower() != rec["term"].lower() and e not in entries:
                    entries.append(e)
            items.append({"ui": rec["ui"], "term": rec["term"], "entries": entries[:12], "trees": rec.get("trees", [])[:6]})

    for line in text.splitlines():
        line = line.strip()
        if line == "*NEWRECORD":
            flush()
            rec = {"entries": [], "trees": []}
            continue
        if " = " not in line:
            continue
        key, _, value = line.partition(" = ")
        if key == "MH":
            rec["term"] = value.strip()
        elif key in ("ENTRY", "PRINT ENTRY"):
            rec.setdefault("entries", []).append(value.split("|", 1)[0].strip())
        elif key == "MN":
            rec.setdefault("trees", []).append(value.strip())
        elif key == "UI":
            rec["ui"] = value.strip()
    flush()
    return items


URL_PATTERNS = (
    "https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/asciimesh/d{year}.bin",
    "https://nlmpubs.nlm.nih.gov/projects/mesh/{year}/asciimesh/d{year}.bin",
)
SPARQL = "https://id.nlm.nih.gov/mesh/sparql"


def _save_head(text: str, source: str) -> None:
    path = catalog_path().parent / "last_response_head.txt"
    path.write_text(f"# kaynak: {source}\n" + (text or "")[:3000], encoding="utf-8")


def _fetch_ascii(client: HttpClient) -> tuple:
    """(metin, yıl, kaynak) döndürür; hiçbir aday geçerli ASCII MeSH değilse (None, None, None)."""
    year = datetime.date.today().year
    for y in (year, year - 1, year - 2):
        for pattern in URL_PATTERNS:
            url = pattern.format(year=y)
            try:
                log.info("MeSH deneniyor: %s", url)
                text = client.request("GET", url).text
            except Exception as exc:  # noqa: BLE001
                log.warning("  alınamadı: %s", exc)
                continue
            if "*NEWRECORD" in text[:5000] and "MH = " in text[:20000]:
                return text, y, url
            log.warning("  beklenmeyen içerik (ASCII MeSH değil), atlanıyor")
            _save_head(text, url)
    return None, None, None


def _sparql(client: HttpClient, query: str) -> list:
    rows: list = []
    offset = 0
    while True:
        q = f"{query}\nLIMIT 1000 OFFSET {offset}"
        data = client.request("GET", SPARQL, params={"query": q, "format": "JSON", "inference": "true"}).json()
        batch = data.get("results", {}).get("bindings", [])
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        offset += 1000


def build_from_sparql(client: HttpClient) -> list:
    """Yedek kaynak: NLM MeSH RDF sorgu servisi. Hastalık dalındaki tanımlayıcılar, ağaç kodları ve eş adlar."""
    prefix = "PREFIX meshv: <http://id.nlm.nih.gov/mesh/vocab#>\nPREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
    q1 = prefix + (
        "SELECT ?d ?label ?tn WHERE { ?d a meshv:TopicalDescriptor ; rdfs:label ?label ; meshv:treeNumber ?t . "
        "?t rdfs:label ?tn . FILTER(STRSTARTS(STR(?tn), \"C\") || STRSTARTS(STR(?tn), \"F03\")) } ORDER BY ?d ?tn"
    )
    items: dict = {}
    for r in _sparql(client, q1):
        ui = r["d"]["value"].rsplit("/", 1)[-1]
        it = items.setdefault(ui, {"ui": ui, "term": r["label"]["value"], "entries": [], "trees": []})
        tn = r["tn"]["value"]
        if tn not in it["trees"]:
            it["trees"].append(tn)
    if not items:
        return []
    q2 = prefix + (
        "SELECT ?d ?syn WHERE { ?d a meshv:TopicalDescriptor ; meshv:treeNumber ?t ; meshv:concept ?c . ?t rdfs:label ?tn . "
        "?c meshv:term ?term . ?term meshv:prefLabel ?syn . "
        "FILTER(STRSTARTS(STR(?tn), \"C\") || STRSTARTS(STR(?tn), \"F03\")) } ORDER BY ?d ?syn"
    )
    for r in _sparql(client, q2):
        ui = r["d"]["value"].rsplit("/", 1)[-1]
        it = items.get(ui)
        syn = r["syn"]["value"]
        if it and syn.lower() != it["term"].lower() and syn not in it["entries"] and len(it["entries"]) < 12:
            it["entries"].append(syn)
    for it in items.values():
        it["trees"] = it["trees"][:6]
    return list(items.values())


def build(client: HttpClient = None, progress=None) -> dict:
    """progress: aşama metni alan isteğe bağlı geri çağırma (arayüzde gösterilir)."""
    client = client or HttpClient()
    report = progress or (lambda stage: None)
    report("NLM'den hastalık sözlüğü indiriliyor (yaklaşık 35 MB)")
    text, year, source = _fetch_ascii(client)
    report("Hastalık terimleri ayrıştırılıyor")
    items = parse_ascii(text) if text else []
    if not items:
        log.info("ASCII MeSH alınamadı; NLM MeSH sorgu servisi (SPARQL) deneniyor...")
        report("Dosya alınamadı; NLM sorgu servisinden parça parça alınıyor (birkaç dakika sürebilir)")
        try:
            items = build_from_sparql(client)
            source, year = SPARQL, datetime.date.today().year
        except Exception as exc:  # noqa: BLE001
            log.warning("SPARQL başarısız: %s", exc)
            items = []
    if len(items) < 1000:
        raise RuntimeError(
            f"MeSH hastalık kataloğu kurulamadı ({len(items)} kayıt). Kaynaklar yanıt vermedi ya da beklenmeyen içerik döndü; "
            f"teşhis için {catalog_path().parent / 'last_response_head.txt'} dosyasına bakın."
        )
    report("Katalog kaydediliyor")
    payload = {"year": year, "source": source, "built_at": datetime.datetime.now().isoformat(timespec="seconds"), "n": len(items), "items": items}
    catalog_path().write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    log.info("MeSH hastalık kataloğu yazıldı: %d kayıt (%s)", len(items), source)
    return payload


_cache: dict = {}


def load() -> dict:
    path = catalog_path()
    if not path.exists():
        return {}
    if _cache.get("mtime") != path.stat().st_mtime:
        data = json.loads(path.read_text(encoding="utf-8"))
        for it in data["items"]:
            it["_norm"] = normalize(it["term"])
            it["_all"] = " | ".join([it["_norm"]] + [normalize(e) for e in it["entries"]])
        _cache.update({"mtime": path.stat().st_mtime, "data": data})
    return _cache["data"]


HEREDITARY_TREES = ("C16.320",)  # Genetic Diseases, Inborn


def lookup(mesh_id: str) -> dict:
    """Katalogdan tek kayıt: {ui, term, entries, trees}; katalog yoksa ya da bulunamazsa {}."""
    ui = (mesh_id or "").split(":")[-1].strip()
    if not ui:
        return {}
    data = load()
    if not data:
        return {}
    for it in data["items"]:
        if it["ui"] == ui:
            return it
    return {}


def is_hereditary(mesh_id: str) -> bool:
    """Hastalık MeSH'te 'Genetic Diseases, Inborn' (C16.320) altında mı? (RP, FMF, kistik fibroz: evet;
    Behçet, RA, hipertansiyon: hayır.) Kalıtsal hastalıkta mutasyonlu genin inhibitörü ters yön sayılır."""
    return any(t.startswith(HEREDITARY_TREES) for t in lookup(mesh_id).get("trees", []))


def search(query: str, limit: int = 12) -> list:
    data = load()
    if not data:
        return []
    toks = tokens(query)
    if not toks:
        return []
    scored = []
    for it in data["items"]:
        hay = it["_all"]
        hits = sum(1 for t in toks if t in hay)
        if hits == 0:
            continue
        score = hits * 10
        if it["_norm"].startswith(toks[0]):
            score += 6
        elif any(part.startswith(toks[0]) for part in hay.split(" | ")):
            score += 3
        if hits == len(toks):
            score += 4
        score -= min(len(it["_norm"]), 60) / 60  # kısa (daha genel) terimler önce
        scored.append((score, it))
    scored.sort(key=lambda x: -x[0])
    return [{"ui": it["ui"], "mesh_id": f"MESH:{it['ui']}", "term": it["term"], "entries": it["entries"][:5], "trees": it["trees"][:3]}
            for _, it in scored[:limit]]


CATEGORIES = {
    "C01": "Enfeksiyon hastalıkları",
    "C04": "Kanserler (neoplazmlar)",
    "C05": "Kas-iskelet hastalıkları",
    "C06": "Sindirim sistemi hastalıkları",
    "C07": "Ağız ve çene hastalıkları",
    "C08": "Solunum yolu hastalıkları",
    "C09": "Kulak-burun-boğaz hastalıkları",
    "C10": "Sinir sistemi hastalıkları",
    "C11": "Göz hastalıkları",
    "C12": "Ürogenital hastalıklar",
    "C13": "Kadın ürogenital ve gebelik hastalıkları",
    "C14": "Kalp-damar hastalıkları",
    "C15": "Kan ve lenf hastalıkları",
    "C16": "Doğumsal, kalıtsal ve yenidoğan hastalıkları",
    "C17": "Deri ve bağ dokusu hastalıkları",
    "C18": "Beslenme ve metabolizma hastalıkları",
    "C19": "Endokrin sistem hastalıkları",
    "C20": "Bağışıklık sistemi hastalıkları",
    "C21": "Çevresel kökenli bozukluklar",
    "C24": "Meslek hastalıkları",
    "C25": "Kimyasal kaynaklı bozukluklar",
    "C26": "Yaralanmalar",
    "F03": "Ruhsal bozukluklar",
}
# C22 (hayvan hastalıkları) ve C23 (belirti/bulgu/patolojik durumlar) listede gösterilmez.
_HIDDEN = ("C22", "C23")


def _visible(item: dict) -> bool:
    trees = item.get("trees", [])
    return any(t[:3] in CATEGORIES for t in trees) and not all(t.startswith(_HIDDEN) for t in trees)


def browse(query: str = "", category: str = "", offset: int = 0, limit: int = 200) -> dict:
    """Kategori ve/veya arama ile süzülmüş, alfabetik tam liste; kategori sayımlarıyla."""
    data = load()
    if not data:
        return {"total": 0, "items": [], "categories": []}
    items = [it for it in data["items"] if _visible(it)]
    if category:
        items = [it for it in items if any(t.startswith(category) for t in it["trees"])]
    toks = tokens(query) if query else []
    if toks:
        items = [it for it in items if all(t in it["_all"] for t in toks) or any(it["_norm"].startswith(t) for t in toks)]
        items.sort(key=lambda it: (0 if it["_norm"].startswith(toks[0]) else 1, it["_norm"]))
    else:
        items.sort(key=lambda it: it["_norm"])
    counts: dict = {}
    for it in items:
        for code in {t[:3] for t in it["trees"] if t[:3] in CATEGORIES}:
            counts[code] = counts.get(code, 0) + 1
    cats = [{"code": c, "name": n, "n": counts.get(c, 0)} for c, n in CATEGORIES.items() if counts.get(c, 0)]
    page = [{"ui": it["ui"], "mesh_id": f"MESH:{it['ui']}", "term": it["term"], "entries": it["entries"][:4],
             "categories": sorted({t[:3] for t in it["trees"] if t[:3] in CATEGORIES})} for it in items[offset: offset + limit]]
    return {"total": len(items), "items": page, "categories": cats}


def status() -> dict:
    data = load()
    ready = bool(data) and data.get("n", 0) >= 1000
    return {"ready": ready, "year": data.get("year"), "n": data.get("n", 0), "built_at": data.get("built_at"), "source": data.get("source")}


def pubmed_query(term: str, entries: list) -> str:
    names = [term] + [e for e in entries if len(e) >= 4][:3]
    tiab = " OR ".join(f'"{n}"[Title/Abstract]' for n in dict.fromkeys(names))
    return f'"{term}"[MeSH Terms] OR {tiab}'
