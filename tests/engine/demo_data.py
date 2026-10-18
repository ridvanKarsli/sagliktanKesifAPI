"""Çevrimdışı test için sentetik derleme (ilişkiler ve ilaç sözlüğü dahil).

Yerleştirilmiş sinyal: 3 "gelecek" ilaç hastalık genleriyle birlikte geçiyor, biri ChEMBL mekanizmasıyla da
bağlı; popüler ilaçlar her gene bağlanarak yöntemi yanıltmaya çalışıyor. Yalnızca KODU sınar.
"""

from __future__ import annotations

import random

from kesif_engine.scoring.ranking import NEG, POS, Rec

DISEASE = "MESH:D000001"


def make_corpus(seed: int = 7):
    rng = random.Random(seed)
    disease_genes = [(f"G{i}", f"GENE{i}") for i in range(1, 7)]
    other_genes = [(f"G{i}", f"GENE{i}") for i in range(7, 41)]
    all_genes = disease_genes + other_genes
    known = [(f"K{i}", f"knowndrug{i}") for i in range(1, 6)]
    future = [("F1", "futuredrug1"), ("F2", "futuredrug2"), ("F3", "futuredrug3")]
    popular = [(f"P{i}", f"popdrug{i}") for i in range(1, 6)]
    noise = [(f"N{i}", f"noisechem{i}") for i in range(1, 601)]

    a_records = []
    for i in range(500):
        year = rng.randint(2000, 2024)
        genes = {gid: lab for gid, lab in disease_genes if rng.random() < 0.30}
        for gid, lab in rng.sample(other_genes, 2):
            genes[gid] = lab
        chems = {cid: lab for cid, lab in known if rng.random() < 0.08}
        if year > 2015:
            chems.update({cid: lab for cid, lab in future if rng.random() < 0.10})
        for cid, lab in rng.sample(noise, 1):
            chems[cid] = lab
        rels = [[POS, "Gene", gid, "Disease", DISEASE] for gid in genes if gid in dict(disease_genes) and rng.random() < 0.5]
        a_records.append(Rec(f"A{i}", year, genes, chems, rels))

    links = {"F1": {"G1", "G2", "G3"}, "F2": {"G2", "G4"}, "F3": {"G5", "G6"}}
    c_by_gene = {}
    for gid, glabel in all_genes:
        recs = []
        for j in range(150):
            year = rng.randint(2000, 2024)
            chems, rels = {}, []
            for cid, lab in future:
                if gid in links[cid] and rng.random() < 0.15:
                    chems[cid] = lab
                    if rng.random() < 0.6:
                        rels.append([NEG, "Chemical", cid, "Gene", gid])
            for cid, lab in popular:
                if rng.random() < 0.25:
                    chems[cid] = lab
            if rng.random() < 0.6:
                chems["W0"] = "water"
            for cid, lab in rng.sample(noise, 3):
                chems[cid] = lab
            recs.append(Rec(f"C{gid}_{j}", year, {gid: glabel}, chems, rels))
        c_by_gene[gid] = recs
    return a_records, c_by_gene


def demo_drugs():
    """Sentetik ChEMBL sözlüğü ve eşleme: gelecek/bilinen/popüler ilaçlar ilaçtır, gürültü değildir."""
    drugs = {}
    drug_map = {}
    for i in range(1, 6):
        drugs[f"CHEMBLK{i}"] = {"chembl_id": f"CHEMBLK{i}", "name": f"knowndrug{i}", "max_phase": 4.0, "synonyms": [], "mechanisms": []}
        drug_map[f"K{i}"] = f"CHEMBLK{i}"
        drugs[f"CHEMBLP{i}"] = {"chembl_id": f"CHEMBLP{i}", "name": f"popdrug{i}", "max_phase": 4.0, "synonyms": [], "mechanisms": []}
        drug_map[f"P{i}"] = f"CHEMBLP{i}"
    for i in range(1, 4):
        mechs = [{"action_type": "INHIBITOR", "moa": "GENE1 inhibitor", "genes": ["GENE1"]}] if i == 1 else []
        drugs[f"CHEMBLF{i}"] = {"chembl_id": f"CHEMBLF{i}", "name": f"futuredrug{i}", "max_phase": 3.0, "synonyms": [], "mechanisms": mechs}
        drug_map[f"F{i}"] = f"CHEMBLF{i}"
    # gürültü kimyasallarının bir kısmı da "ilaç" olsun ki evren yalnızca sinyalden oluşmasın
    for i in range(1, 201):
        drugs[f"CHEMBLN{i}"] = {"chembl_id": f"CHEMBLN{i}", "name": f"noisechem{i}", "max_phase": 2.0, "synonyms": [], "mechanisms": []}
        drug_map[f"N{i}"] = f"CHEMBLN{i}"
    return drugs, drug_map
