"""Doğrulama etiketlerinin elle denetimi.

Geriye dönük testin doğruluğu etiketin doğruluğuyla sınırlıdır: "kesitten sonra ilk kez klinik çalışmaya girdi" denen
bir ilaç aslında arka plan tedavisi, görüntüleme ajanı ya da eski bir ilacın tuz biçimiyse, ölçüm yanlış yöne çeker.
Bu modül her hastalıktan rastgele bir örnek çıkarır (her satırda kaynağı olan klinik çalışmanın bağlantısıyla),
işaretlenmiş örneği geri okur ve hata oranını hesaplar. Yanlış işaretlenen ilaçlar ölçümden çıkarılır.

Dosya: data/<hastalık>/label_audit.json → {"n_checked", "n_wrong", "error_rate", "wrong": [chembl_id], "at"}
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import random
from pathlib import Path
from typing import Dict, List, Optional

FILE = "label_audit.json"
COLUMNS = ["chembl_id", "ilac", "ilk_yil", "faz", "calisma", "mudahale_adi", "baglanti", "dogru_mu", "not"]
YES = {"evet", "e", "doğru", "dogru", "1", "yes", "y", "true", "x"}
NO = {"hayır", "hayir", "h", "yanlış", "yanlis", "0", "no", "n", "false"}
MIN_YEAR = 2010  # pozitifler 2012–2018 kesitlerinden sonra doğar; denetim bu bölgeye odaklanır


def sample(labels: dict, drugs: List[dict], n: int = 50, seed: int = 20261020, min_year: int = MIN_YEAR) -> List[dict]:
    """Denetlenecek satırlar: ilk klinik çalışma yılı min_year ve sonrası olan ilaçlardan rastgele (sabit tohum)."""
    names = {d["chembl_id"]: d.get("name", "") for d in drugs}
    sources = labels.get("first_trial_source") or {}
    pool = sorted(k for k, v in sources.items() if (v.get("year") or 0) >= min_year)
    rng = random.Random(seed)
    picked = sorted(rng.sample(pool, min(n, len(pool))), key=lambda k: (sources[k]["year"], k))
    rows = []
    for k in picked:
        src = sources[k]
        nct = src.get("nct") or ""
        rows.append({
            "chembl_id": k, "ilac": (names.get(k) or k).lower(), "ilk_yil": src.get("year"), "faz": src.get("phase") or "",
            "calisma": nct, "mudahale_adi": src.get("name", ""),
            "baglanti": f"https://clinicaltrials.gov/study/{nct}" if nct else "", "dogru_mu": "", "not": "",
        })
    return rows


def to_csv(rows: List[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, delimiter=";")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in COLUMNS})
    return buf.getvalue()


def read_marked(text: str) -> dict:
    """İşaretlenmiş CSV'yi okur (ayraç ; ya da ,). Boş bırakılan satırlar sayılmaz."""
    first = text.splitlines()[0] if text.strip() else ""
    delim = ";" if first.count(";") >= first.count(",") else ","
    checked, wrong, unknown = 0, [], []
    for row in csv.DictReader(io.StringIO(text), delimiter=delim):
        mark = (row.get("dogru_mu") or "").strip().lower()
        if not mark:
            continue
        if mark in YES:
            checked += 1
        elif mark in NO:
            checked += 1
            wrong.append({"chembl_id": row.get("chembl_id"), "ilac": row.get("ilac"), "not": row.get("not") or ""})
        else:
            unknown.append(mark)
    if unknown:
        raise ValueError(f"Tanınmayan işaret(ler): {', '.join(sorted(set(unknown))[:5])}. 'evet' ya da 'hayır' yazın.")
    return {"n_checked": checked, "n_wrong": len(wrong), "error_rate": round(len(wrong) / checked, 4) if checked else None,
            "wrong": wrong, "at": datetime.datetime.now().isoformat(timespec="seconds")}


def merge(previous: Optional[dict], new: dict) -> dict:
    """Aynı hastalıkta birden fazla denetim turu: sayılar toplanır, yanlış listesi birleşir."""
    if not previous:
        return new
    wrong = {w["chembl_id"]: w for w in previous.get("wrong", [])}
    for w in new.get("wrong", []):
        wrong[w["chembl_id"]] = w
    n_checked = int(previous.get("n_checked", 0)) + int(new["n_checked"])
    n_wrong = len(wrong)
    return {"n_checked": n_checked, "n_wrong": n_wrong, "error_rate": round(n_wrong / n_checked, 4) if n_checked else None,
            "wrong": sorted(wrong.values(), key=lambda w: w["chembl_id"] or ""), "at": new["at"],
            "rounds": int(previous.get("rounds", 1)) + 1}


def load(data_dir: Path) -> Dict:
    path = Path(data_dir) / FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save(data_dir: Path, audit: dict) -> None:
    (Path(data_dir) / FILE).write_text(json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")


def excluded_ids(audit: Optional[dict]) -> set:
    return {w["chembl_id"] for w in (audit or {}).get("wrong", []) if w.get("chembl_id")}
