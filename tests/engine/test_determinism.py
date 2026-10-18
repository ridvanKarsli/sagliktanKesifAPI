"""Tekrarlanabilirlik: aynı veri her çalıştırmada aynı sıralamayı ve aynı kanıtı vermeli.

Python küme (set) sırası süreçten sürece değişir (PYTHONHASHSEED). Sıralama, eşitlik çözümü, ana kimlik seçimi ve
mekanizma metni bu sıraya bağlı olursa aynı derlem farklı çıktılar üretir. Bu test aynı derlemi farklı tohumlarla
ayrı süreçlerde puanlar ve çıktıların bayt bayt aynı olduğunu doğrular.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

SCRIPT = r"""
import hashlib, json
from kesif_engine.scoring.ranking import Rec, known_therapies, rank_candidates
from tests.engine.demo_data import DISEASE, demo_drugs, make_corpus

a, c = make_corpus()
drugs, drug_map = demo_drugs()
# Aynı ilacın iki kimliği (MeSH + metin taraması) eşit kanıtla: ana kimlik seçimi küme sırasına bağlı olmamalı
for rec in a[:40]:
    if "F1" in rec.chemicals:
        rec.chemicals["CHEMBL:" + drug_map["F1"]] = rec.chemicals["F1"]
drug_map["CHEMBL:" + drug_map["F1"]] = drug_map["F1"]
ranked, evidence = rank_candidates(a, c, drugs=drugs, drug_map=drug_map, disease_id=DISEASE)
known = known_therapies(a, drugs=drugs, drug_map=drug_map, disease_id=DISEASE)
blob = ranked.to_csv() + json.dumps(evidence, sort_keys=True) + known.to_csv()
print(hashlib.sha256(blob.encode()).hexdigest())
"""


def _run(seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": os.pathsep.join([str(BACKEND), str(BACKEND / "src")])}
    out = subprocess.run([sys.executable, "-c", SCRIPT], cwd=BACKEND, env=env, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_ranking_is_identical_across_hash_seeds():
    digests = {_run(seed) for seed in ("1", "2", "3", "42")}
    assert len(digests) == 1, "aynı veri farklı süreçlerde farklı sıralama/kanıt üretti"
    assert len(next(iter(digests))) == hashlib.sha256().digest_size * 2
