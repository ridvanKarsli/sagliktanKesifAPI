"""Hekim/araştırmacı değerlendirmesi için tek dosyalık HTML rapor üretir."""

from __future__ import annotations

import datetime
import html
import json
from pathlib import Path

import pandas as pd

PUBMED = "https://pubmed.ncbi.nlm.nih.gov/{}/"
CHEMBL = "https://www.ebi.ac.uk/chembl/compound_report_card/{}/"

TR = {
    "consistent": "tutarlı",
    "opposite": "ters",
    "unknown": "bilinmiyor",
    "up": "artmış",
    "down": "azalmış",
    "inhibit": "inhibe eder",
    "activate": "aktive eder",
}

CSS = """
body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:24px;color:#1b1b1b;background:#fff}
h1{font-size:22px;margin:0 0 4px}.sub{color:#666;font-size:13px;margin-bottom:18px}
.warn{background:#fff4e5;border-left:4px solid #e08a00;padding:10px 14px;margin:14px 0;font-size:14px}
.box{background:#f5f7fa;border-radius:8px;padding:12px 16px;margin:12px 0;font-size:14px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid #e3e3e3;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f0f2f5;position:sticky;top:0}tr:hover{background:#fafbfc}
.tag{display:inline-block;padding:1px 7px;border-radius:10px;font-size:12px}
.tutarlı{background:#e3f6e8;color:#13652a}.ters{background:#fde7e7;color:#8f1d1d}.karışık{background:#fff1cc;color:#6b4e00}.bilinmiyor{background:#eee;color:#555}
details{margin:4px 0}summary{cursor:pointer;color:#1a56b0}.ev{font-size:12px;color:#333;margin:4px 0 8px 12px}
a{color:#1a56b0;text-decoration:none}a:hover{text-decoration:underline}
@media (prefers-color-scheme:dark){body{background:#121417;color:#e6e6e6}th{background:#1e2227}td,th{border-color:#2c3138}
.box{background:#1b1f24}.warn{background:#3a2a10}tr:hover{background:#181c21}a{color:#7fb0ff}summary{color:#7fb0ff}.bilinmiyor{background:#2c3138;color:#bbb}}
"""


def _links(pmids) -> str:
    return ", ".join(f'<a href="{PUBMED.format(p)}" target="_blank">{html.escape(str(p))}</a>' for p in pmids)


def _evidence_html(links: list) -> str:
    parts = []
    for l in links:
        gene = html.escape(l["gene"])
        gdir = TR.get(l.get("gene_direction", "unknown"), "bilinmiyor")
        cons = TR.get(l.get("consistency", "unknown"), "bilinmiyor")
        if l.get("source") == "chembl":
            parts.append(
                f'<div class="ev"><b>{gene}</b> (ChEMBL mekanizma: {html.escape(l.get("action_type", "").lower())}, '
                f'{html.escape(l.get("moa", ""))}) · gen hastalıkta: {gdir} · yön: <span class="tag {cons}">{cons}</span>'
                f'<br>Hastalık-gen makaleleri: {_links(l.get("ab_pmids", []))}</div>'
            )
        else:
            rel = l.get("relations", {})
            effect = TR.get(l.get("drug_effect", "unknown"), "bilinmiyor")
            parts.append(
                f'<div class="ev"><b>{gene}</b> · hastalık makalesi: {l["n_ab"]} · ilaçla birlikte: {l["n_bc"]} '
                f'(lift {l.get("lift", "")}) · gen hastalıkta: {gdir} · ilaç geni: {effect} '
                f'(neg {rel.get("Negative_Correlation", 0)}, poz {rel.get("Positive_Correlation", 0)}) · '
                f'yön: <span class="tag {cons}">{cons}</span>'
                f'<br>Hastalık-gen: {_links(l.get("ab_pmids", []))}<br>Gen-ilaç: {_links(l.get("bc_pmids", []))}</div>'
            )
    return "".join(parts)


def build_report(results_dir: Path, disease_cfg: dict, top_n: int = 50) -> Path:
    cand = pd.read_csv(results_dir / "candidates.csv", index_col="rank")
    novelty_path = results_dir / "candidates_novelty.csv"
    if novelty_path.exists():
        nov = pd.read_csv(novelty_path, index_col="rank")[["chemical_id", "clinical_trials", "pubmed_direct", "durum"]]
        cand = cand.merge(nov, on="chemical_id", how="left").set_index(cand.index[: len(cand)])
    evidence = json.loads((results_dir / "evidence.json").read_text(encoding="utf-8"))
    bt_path = results_dir / "backtest.json"
    bt = json.loads(bt_path.read_text(encoding="utf-8")) if bt_path.exists() else None

    # raporda endojen moleküller ve tanı ajanları listelenmez (arayüzdeki varsayılan görünümle aynı)
    if "kind" in cand.columns:
        cand = cand[~cand["kind"].isin(["endojen", "tanı/yardımcı"])]
    rows = []
    for rank, r in cand.head(top_n).iterrows():
        chembl = (
            f'<a href="{CHEMBL.format(r["chembl_id"])}" target="_blank">{html.escape(str(r["chembl_id"]))}</a>'
            if isinstance(r.get("chembl_id"), str) and r["chembl_id"]
            else ""
        )
        durum = html.escape(str(r["durum"])) if "durum" in r and isinstance(r["durum"], str) else "kontrol edilmedi"
        status = html.escape(str(r["status"])) if "status" in r and isinstance(r.get("status"), str) else ""
        kind = str(r.get("kind", "")) if isinstance(r.get("kind"), str) else ""
        kind_html = f"<br><small>{html.escape(kind)}</small>" if kind and kind != "ilaç" else ""
        pen = float(r["penalty"]) if "penalty" in r and not pd.isna(r.get("penalty")) else 1.0
        pen_html = f" · ceza ×{pen:.2f}" if pen < 1 else ""
        phase = r.get("max_phase", "")
        phase = "" if pd.isna(phase) else f"{float(phase):g}"
        rows.append(
            f"<tr><td>{rank}</td><td><b>{html.escape(str(r['chemical']))}</b>{kind_html}<br>{chembl}</td><td>{phase}</td>"
            f"<td>{r['score']:.1f}<br><small>lit {r['lit_score']:.1f} · mek {r['mech_score']:.1f}{pen_html}</small></td>"
            f'<td><span class="tag {r["direction"]}">{r["direction"]}</span></td>'
            f"<td>{html.escape(str(r['mechanism']) if isinstance(r['mechanism'], str) else '')}</td>"
            f"<td>{status}{'<br>' if status else ''}{durum}<br><small>hastalık makalesi: {int(r['n_direct_a'])}</small></td>"
            f"<td><details><summary>{int(r['n_bridges'])} köprü</summary>{_evidence_html(evidence.get(r['chemical_id'], []))}</details></td></tr>"
        )

    bt_html = ""
    if bt and bt.get("schemes"):
        scheme_names = {
            "mentions": "ilk anılma (gürültülü)",
            "therapy": "tedavi makalesi",
            "trials": "klinik çalışma",
            "trials_novel": "klinik çalışma, önce hiç anılmamış",
        }
        rows_bt = []
        for scheme, res in bt["schemes"].items():
            label = scheme_names.get(scheme, scheme)
            if "metrics" not in res:
                rows_bt.append(f"<tr><td>{label}</td><td colspan=5>{html.escape(res.get('note', ''))}</td></tr>")
                continue
            rnd = res["random_expected_hits"]
            for key, m in res["metrics"].items():
                rows_bt.append(
                    f"<tr><td>{label}</td><td>{key}</td><td>{m['auc']}</td><td>{m['hits_at_10']} / {rnd['at_10']}</td>"
                    f"<td>{m['hits_at_25']} / {rnd['at_25']}</td><td>{res['n_positives']} / {res['n_universe']}</td></tr>"
                )
        bt_html = (
            f'<div class="box"><b>Geriye dönük doğrulama (kesit {bt["cutoff_year"]}, birincil şema: '
            f'{scheme_names.get(bt.get("primary_scheme", ""), "")})</b><br><small>İsabet sütunları "bulunan / rastgele beklenen". '
            f'Yöntem, frequency satırını geçmiyorsa öngörü gücü iddia edilemez.</small>'
            f'<table style="margin-top:8px"><thead><tr><th>Etiket</th><th>Sıralama</th><th>AUC</th><th>isabet@10</th>'
            f'<th>isabet@25</th><th>pozitif / evren</th></tr></thead><tbody>{"".join(rows_bt)}</tbody></table></div>'
        )
    elif bt:
        bt_html = f'<div class="box">Geriye dönük doğrulama: {html.escape(bt.get("note", "yapılmadı"))}</div>'

    disc_html = ""
    disc_path = results_dir / "discovery.json"
    if disc_path.exists():
        d = json.loads(disc_path.read_text(encoding="utf-8"))
        items = "".join(
            f"<li><b>#{c['rank']} {html.escape(c['chemical'])}</b>"
            + (f" (faz {c['max_phase']:.0f})" if c.get("max_phase") is not None else "")
            + (f" · <i>ClinicalTrials.gov kaydı: {c['clinical_trials']}</i>" if c.get("clinical_trials") else "")
            + f"<br><small>{html.escape(c.get('justification', ''))}</small></li>"
            for c in d.get("new_candidates", [])[:15]
        )
        classes = ", ".join(f"{html.escape(c['gene'])} ({c['n']})" for c in d.get("target_classes", [])[:6])
        conf = d.get("confidence") or {}
        conf_html = ""
        if conf.get("grade"):
            reasons = " ".join(html.escape(r) for r in conf.get("reasons", []))
            conf_html = f'<div class="box"><b>Bu hastalıkta yönteme güven: {html.escape(conf["grade"])}.</b> <small>{reasons}</small></div>'
        disc_html = conf_html + (
            f'<div class="box"><b>Keşif özeti.</b> {html.escape(d.get("verdict", ""))}'
            + (f"<br><small>Öne çıkan hedef sınıfları: {classes}</small>" if classes else "")
            + f'<ol style="margin:8px 0 0;padding-left:20px">{items}</ol></div>'
        )

    page = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(disease_cfg['label'])} ilaç hipotezleri</title><style>{CSS}</style></head><body>
<h1>{html.escape(disease_cfg['label'])}: literatür tabanlı ilaç yeniden konumlandırma hipotezleri</h1>
<div class="sub">Üretim: {datetime.datetime.now():%Y-%m-%d %H:%M} · {len(cand)} aday, ilk {min(top_n, len(cand))} gösteriliyor</div>
<div class="warn"><b>Bu rapor tedavi önerisi değildir.</b> Satırlar, makalelerdeki birlikte geçiş ve ChEMBL mekanizma
verisinden türetilmiş araştırma hipotezleridir. Birlikte geçiş nedensellik göstermez; "yön" alanı otomatik çıkarımdır
ve hatalı olabilir. Her aday klinik uzman tarafından değerlendirilmelidir.</div>
{disc_html}
{bt_html}
<div class="box">Sütunlar: <b>puan</b> = literatür köprü puanı + mekanizma puanı · <b>yön</b>: tutarlı = gen hastalıkta artmış ve ilaç geni
inhibe ediyor (veya tersi), ters = aksi, bilinmiyor = ilişki verisi yetersiz · <b>durum</b>: klinik çalışma kayıtları ve literatürde doğrudan kontrol.</div>
<table><thead><tr><th>#</th><th>İlaç</th><th>Faz</th><th>Puan</th><th>Yön</th><th>Mekanizma (köprü genlerde)</th><th>Yenilik durumu</th><th>Kanıt</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p class="sub">Kaynaklar: açık biyomedikal literatür, ilaç ve klinik çalışma veritabanları. Sağlıktan Keşif.</p>
</body></html>"""
    out = results_dir / "report.html"
    out.write_text(page, encoding="utf-8")
    return out
