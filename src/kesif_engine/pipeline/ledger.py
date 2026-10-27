"""Tahmin defteri (Faz 4): bugünün yeni adaylarını değiştirilemez biçimde dondurmak ve ileride bağımsız doğrulamayı ölçmek.

Neden: geriye dönük test "geçmişi tahmin etti" der; buluş iddiası ise "geleceği tahmin etti" kanıtı ister. Bunun tek
itiraz edilemez yolu, hipotezleri doğrulama gelmeden ÖNCE zaman damgasıyla kaydetmektir.

Dondurma (freeze):
    Her hastalık için sıralamanın ilk N "yeni" adayı: ilaç ya da besin/takviye; hastalıkta hiç klinik çalışmaya
    girmemiş (temizlenmiş etiketler VE klinik çalışma atlasında hastalığın kendi MeSH kodu); literatürde "tedavi
    olarak çalışılmış" değil. Ayrıca aynı kurallardan geçen bütün adayların kimlikleri (evren) saklanır: ileride
    "rastgele 20 aday seçilseydi kaçı doğrulanırdı" beklentisi bu evrenden hesaplanır.

    data/ledger/<defter>/entries.json   kanonik JSON (anahtarlar sıralı, boşluksuz, UTF-8); içerik
    data/ledger/<defter>/manifest.json  oluşturma zamanı (UTC), entries.json'un SHA-256 özeti, yöntem ve veri sürümleri

Gizlilik ve patent: defterin İÇERİĞİ yayımlanmaz (yayın, patent başvurusundan önce yeniliği bozar). Kamuya yalnızca
SHA-256 özeti verilir (ör. OpenTimestamps ile zaman damgası); özet içerik hakkında hiçbir şey açığa vurmaz ama içeriğin
o tarihte var olduğunu kanıtlar. İçerik, patent başvurusundan sonra açılır ve herkes özeti yeniden hesaplayabilir.

Değerlendirme (evaluate): sonraki bir atlas ya da etiket yenilemesinde, defterdeki adaylardan dondurma tarihinden
SONRA hastalıkta klinik çalışmaya girenler sayılır ve evrenin aynı dönemdeki oranıyla (rastgele beklenti) kıyaslanır.
"""

from __future__ import annotations

import datetime
import hashlib
import importlib
import json
import logging
import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from kesif_engine.catalog import diseases
from kesif_engine.core import config
from kesif_engine.core.paths import resultsdir, workdir
from kesif_engine.evaluation import labels as labels_mod
from kesif_engine.scoring import fusion
from kesif_engine.sources import trial_atlas

log = logging.getLogger(__name__)

SCHEMA = 1
TOP_N = 20
ELIGIBLE_KINDS = {"ilaç", "besin/takviye"}
STUDIED = "tedavi olarak çalışılmış"
CHANNEL_COLS = ("accel_score", "similar_score", "direct_score", "genetic_score", "network_score")


def ledger_root() -> Path:
    p = config.DATA_DIR / "ledger"
    p.mkdir(parents=True, exist_ok=True)
    return p


def canonical_bytes(obj) -> bytes:
    """Özetlenen bayt dizisi: aynı içerik her zaman aynı baytları verir (anahtar sırası, boşluk, kodlama sabit)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _code(mesh_id: str) -> str:
    return (mesh_id or "").split(":")[-1].strip().upper()


def _tried_in_disease(disease: str, mesh_id: str, atlas: Optional[trial_atlas.TrialAtlas]) -> set:
    lab = labels_mod.load(workdir(disease) / "labels.json") or {}
    tried = set((lab.get("first_trial_year") or {}).keys())
    if atlas is not None:
        tried |= set(atlas.first_year.get(_code(mesh_id), {}).keys())
    return tried


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 6)


def _str(v) -> str:
    return v if isinstance(v, str) else ""


def select(candidates: pd.DataFrame, tried: set, top: int = TOP_N) -> tuple:
    """(defter satırları, evren kimlikleri). candidates: sıralı aday tablosu (results/<h>/candidates.csv)."""
    df = candidates.reset_index() if "rank" not in candidates.columns else candidates
    df = df.sort_values("rank") if "rank" in df.columns else df
    entries: List[dict] = []
    universe: List[str] = []
    seen = set()
    for row in df.to_dict("records"):
        cid = _str(row.get("chembl_id"))
        if not cid or cid in seen:
            continue
        seen.add(cid)
        if row.get("kind") not in ELIGIBLE_KINDS or row.get("status") == STUDIED or cid in tried:
            continue
        universe.append(cid)
        if len(entries) < top:
            entries.append({
                "position": len(entries) + 1,
                "chembl_id": cid,
                "name": _str(row.get("chemical")),
                "kind": row.get("kind"),
                "overall_rank": int(row["rank"]) if row.get("rank") is not None else None,
                "score": _num(row.get("score")),
                "literature_status": _str(row.get("status")),
                "channels": {c.replace("_score", ""): _num(row.get(c)) for c in CHANNEL_COLS if c in row},
                "top_bridges": _str(row.get("top_bridges"))[:300],
                "similar_from": _str(row.get("similar_from")),
                "mechanism": _str(row.get("mechanism")),
            })
    return entries, sorted(universe)


def freeze(keys: Iterable[str], top: int = TOP_N, ledger_id: Optional[str] = None, note: str = "") -> dict:
    """Defteri oluşturur. Aynı kimlikle defter varsa üzerine yazmaz (değiştirilemezlik)."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    ledger_id = ledger_id or now.strftime("%Y-%m-%d")
    root = ledger_root() / ledger_id
    if (root / "manifest.json").exists():
        raise FileExistsError(f"Defter zaten var: {ledger_id} (dondurulmuş defter değiştirilmez)")
    atlas = trial_atlas.load()
    decision = fusion.current_decision()
    per_disease: Dict[str, dict] = {}
    skipped: Dict[str, str] = {}
    for key in sorted(set(keys)):
        path = resultsdir(key) / "candidates.csv"
        if not path.exists():
            skipped[key] = "sıralama yok"
            continue
        cfg = diseases.get(key)
        lab = labels_mod.load(workdir(key) / "labels.json") or {}
        if not lab:
            skipped[key] = "doğrulama etiketi yok (yenilik denetlenemez)"
            continue
        cands = pd.read_csv(path)
        fusion_used = sorted({str(x) for x in cands.get("fusion", pd.Series(dtype=str)).dropna().unique()})
        tried = _tried_in_disease(key, cfg.get("mesh_id", ""), atlas)
        entries, universe = select(cands, tried, top)
        idx = json.loads((workdir(key) / "corpus_index.json").read_text(encoding="utf-8")) if (workdir(key) / "corpus_index.json").exists() else {}
        per_disease[key] = {
            "label": cfg.get("label"), "name_en": cfg.get("name_en"), "mesh_id": cfg.get("mesh_id"),
            "data_until": idx.get("data_until"), "labels_built_at": lab.get("built_at"),
            "fusion": fusion_used, "n_universe": len(universe), "universe": universe, "entries": entries,
        }
    if not per_disease:
        raise RuntimeError("Dondurulacak hastalık yok: " + ", ".join(f"{k} ({v})" for k, v in skipped.items()))
    body = {
        "schema": SCHEMA, "ledger_id": ledger_id, "frozen_at_utc": now.isoformat(), "top_n": top,
        "rules": "ilaç ya da besin/takviye; hastalıkta klinik çalışmaya girmemiş (etiketler + atlas); 'tedavi olarak "
                 "çalışılmış' değil; sıralamadaki ilk N",
        "engine_version": getattr(importlib.import_module("kesif_engine"), "__version__", ""),
        "decision": {"method": decision.get("method"), "measured_as": decision.get("measured_as"),
                     "model_sha256": sha256(canonical_bytes(decision.get("model") or {}))},
        "atlas": atlas.meta if atlas is not None else None,
        "diseases": per_disease,
    }
    data = canonical_bytes(body)
    digest = sha256(data)
    root.mkdir(parents=True, exist_ok=False)
    (root / "entries.json").write_bytes(data)
    manifest = {"ledger_id": ledger_id, "frozen_at_utc": now.isoformat(), "sha256": digest, "bytes": len(data),
                "n_diseases": len(per_disease), "n_entries": sum(len(v["entries"]) for v in per_disease.values()),
                "skipped": skipped, "note": note, "public_commitment": "yalnızca sha256 yayımlanır; içerik patent başvurusundan sonra"}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (root / "entries.sha256").write_text(f"{digest}  entries.json\n", encoding="utf-8")
    log.info("Tahmin defteri %s donduruldu: %d hastalık, %d aday; SHA-256 %s", ledger_id, manifest["n_diseases"],
             manifest["n_entries"], digest)
    return manifest


def verify(ledger_id: str) -> dict:
    root = ledger_root() / ledger_id
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    digest = sha256((root / "entries.json").read_bytes())
    return {"ledger_id": ledger_id, "ok": digest == manifest["sha256"], "sha256": digest, "expected": manifest["sha256"]}


def list_ledgers() -> List[dict]:
    out = []
    for p in sorted(ledger_root().glob("*/manifest.json")):
        out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


def evaluate(ledger_id: str, atlas: Optional[trial_atlas.TrialAtlas] = None) -> dict:
    """Defter adaylarından dondurmadan SONRAKİ bir yılda hastalıkta ilk kez klinik çalışmaya girenler, rastgele
    beklentiyle (aynı evrenin aynı dönemdeki oranı) birlikte. Atlas yıllık çözünürlüktedir: dondurma yılından sonraki
    yıllar sayılır (dondurma yılının kendisi, tarihten önce mi sonra mı başladığı ayrılamadığı için sayılmaz)."""
    atlas = atlas or trial_atlas.load()
    if atlas is None:
        raise RuntimeError("Klinik çalışma atlası yok: önce 'kesif atlas'.")
    body = json.loads((ledger_root() / ledger_id / "entries.json").read_text(encoding="utf-8"))
    year0 = int(body["frozen_at_utc"][:4])
    rows = []
    tot_hits = tot_n = tot_exp = 0.0
    for key, d in body["diseases"].items():
        later = {c for c, y in atlas.first_year.get(_code(d.get("mesh_id", "")), {}).items() if y > year0}
        uni = d.get("universe") or []
        rate = (sum(1 for c in uni if c in later) / len(uni)) if uni else 0.0
        hits = [e["chembl_id"] for e in d["entries"] if e["chembl_id"] in later]
        n = len(d["entries"])
        rows.append({"disease": key, "n": n, "hits": len(hits), "hit_ids": hits, "expected_random": round(rate * n, 3)})
        tot_hits += len(hits)
        tot_n += n
        tot_exp += rate * n
    return {"ledger_id": ledger_id, "counted_after_year": year0, "atlas": atlas.meta, "diseases": rows,
            "hits": int(tot_hits), "n": int(tot_n), "expected_random": round(tot_exp, 3),
            "enrichment": round(tot_hits / tot_exp, 2) if tot_exp else None}


def _binom_tail(k: int, n: int, p: float) -> float:
    """P(X ≥ k), X ~ Binom(n, p): rastgele seçilmiş n adaydan en az k'sının doğrulanma olasılığı."""
    if k <= 0:
        return 1.0
    if p <= 0:
        return 0.0
    return float(min(1.0, sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))))


def track(ledger_id: str, trials: Iterable[dict], drugs: list, min_phase: float) -> dict:
    """Gün çözünürlüklü takip: defter dondurulduktan SONRA ClinicalTrials.gov'a kaydedilen çalışmalardan, etiketlerle
    aynı kurallarla (labels.map_trials_to_drugs: hastalığa ait, tedavi amaçlı, test edilen ilaç…) hangi defter adayları
    hastalıkta klinik çalışmaya girdi? Rastgele beklenti: aynı hastalığın aday evreninin aynı dönemdeki oranı.

    Dondurmadan ÖNCE de aynı hastalıkta çalışması bulunan adaylar (etiket eksikliği yüzünden defterde "yeni" görünenler)
    hem isabetten hem evrenden çıkarılır ve ayrıca raporlanır: ölçüm yalnız gerçekten yeni olanlarla yapılır."""
    body = json.loads((ledger_root() / ledger_id / "entries.json").read_text(encoding="utf-8"))
    frozen = body["frozen_at_utc"][:10]
    trials = list(trials)
    dated = sum(1 for t in trials if t.get("first_submitted"))
    if not trials or dated < 0.5 * len(trials):
        raise RuntimeError(f"Ham çalışma dosyası kullanılamaz: {len(trials)} çalışma, {dated} tanesinde kayıt tarihi var. "
                           "ClinicalTrials.gov kayıtlarını yeniden indirin (kesif ledger track --download).")
    after = [t for t in trials if (t.get("first_submitted") or "") > frozen]
    before = [t for t in trials if t.get("first_submitted") and t["first_submitted"] <= frozen]
    rows = []
    tot = {"hits": 0, "n": 0, "expected": 0.0, "already": 0}
    for key, d in sorted(body["diseases"].items()):
        mesh_id, name = d.get("mesh_id", ""), d.get("name_en", "")
        code = _code(mesh_id)

        def about(ts: List[dict], code: str = code) -> List[dict]:  # hız için önce MeSH koduyla süz (kurallar da kodu ister)
            return [t for t in ts if code in {str(m).upper() for m in (t.get("condition_mesh") or [])}]

        a, b = about(after), about(before)
        new = labels_mod.map_trials_to_drugs(a, drugs, min_phase, mesh_id=mesh_id, name_en=name) if a else {}
        old = labels_mod.map_trials_to_drugs(b, drugs, min_phase, mesh_id=mesh_id, name_en=name) if b else {}
        already = {e["chembl_id"] for e in d["entries"] if e["chembl_id"] in old}
        uni = [c for c in d.get("universe") or [] if c not in old]
        entries = [e for e in d["entries"] if e["chembl_id"] not in already]
        rate = (sum(1 for c in uni if c in new) / len(uni)) if uni else 0.0
        hits = [{"chembl_id": e["chembl_id"], "name": e["name"], "position": e["position"]} for e in entries if e["chembl_id"] in new]
        rows.append({"disease": key, "n": len(entries), "hits": hits, "expected_random": round(rate * len(entries), 3),
                     "already_trialed_before_freeze": sorted(already)})
        tot["hits"] += len(hits)
        tot["n"] += len(entries)
        tot["expected"] += rate * len(entries)
        tot["already"] += len(already)
    p0 = tot["expected"] / tot["n"] if tot["n"] else 0.0
    return {"ledger_id": ledger_id, "frozen": frozen, "checked_at": datetime.date.today().isoformat(),
            "n_trials_after_freeze": len(after), "hits": tot["hits"], "n": tot["n"],
            "expected_random": round(tot["expected"], 3),
            "enrichment": round(tot["hits"] / tot["expected"], 2) if tot["expected"] else None,
            "p_value": round(_binom_tail(tot["hits"], tot["n"], p0), 6),
            "excluded_already_trialed": tot["already"], "diseases": rows}


def _drug_names() -> Dict[str, str]:
    from kesif_engine.sources import chembl

    return {d["chembl_id"]: (d.get("name") or "").lower() for d in chembl.load()}


def blind_panel(ledger_id: str, diseases_: Optional[Iterable[str]] = None, per_disease: int = 10, seed: int = 20261027) -> tuple:
    """Uzman paneli için kör değerlendirme formu (Faz 4). Her hastalıktan yarısı defter adayı, yarısı aynı hastalığın aday
    evreninden rastgele seçilmiş (defterde olmayan) kontrol ilaç; sıra karıştırılır, kaynak ve sıra bilgisi formda yoktur.
    Dönen: (form satırları, anahtar satırları). Anahtar ayrı saklanır ve puanlama bitince açılır: "makul" oranı defter
    adaylarında kontrollerden anlamlı yüksekse sistemin sinyali uzman gözünde de vardır."""
    import random

    body = json.loads((ledger_root() / ledger_id / "entries.json").read_text(encoding="utf-8"))
    rng = random.Random(seed)
    form, key = [], []
    wanted = list(diseases_) if diseases_ else sorted(body["diseases"])
    half = per_disease // 2
    for dz in wanted:
        d = body["diseases"].get(dz)
        if not d:
            continue
        top = d["entries"][:]
        rng.shuffle(top)
        picks = [("defter", e["chembl_id"], e["name"]) for e in top[:half]]
        in_ledger = {e["chembl_id"] for e in d["entries"]}
        pool = [c for c in d.get("universe") or [] if c not in in_ledger]
        names = _drug_names()
        for c in rng.sample(pool, min(per_disease - len(picks), len(pool))):
            picks.append(("kontrol", c, names.get(c, "")))
        rng.shuffle(picks)
        for src, cid, name in picks:
            item = f"{dz}-{len(form) + 1:04d}"
            form.append({"kod": item, "hastalik": d.get("label") or dz, "ilac_chembl": cid, "ilac": name,
                         "degerlendirme (makul / makul değil / zaten biliniyor)": "", "gerekce": ""})
            key.append({"kod": item, "kaynak": src, "hastalik": dz, "ilac_chembl": cid})
    return form, key
