"""Yöntem güveni: bu hastalıkta sıralamaya ne kadar güvenilebilir?

Her hastalıkta yöntem aynı derecede işlemez. Behçet ve FMF gibi inflamatuvar hastalıklarda literatür köprüsü güçlü
sinyal taşırken, retinitis pigmentosa gibi monogenik dejeneratif hastalıklarda geriye dönük testte rastgeleye yakın
kalır. Kullanıcıya her listede aynı güveni göstermek yanıltıcı olur; bu modül hastalık başına ölçüme dayalı bir not verir.

Not yalnızca ölçülmüş sayılardan çıkar (kural tabanlı, eşikler aşağıda sabit):

    yüksek       AP ≥ 5 × rastgele  ve  AP ≥ sıklık sıralaması  ve  ≥ 5 pozitif  ve  ilk 25'te ≥ 1 isabet
    orta         AP ≥ 2 × rastgele  ve  AP ≥ 0,8 × sıklık sıralaması  ve  ilk 25'te ≥ 1 isabet (ortalama ≥ 0,5)
    düşük        diğer durumlar (yöntem bu hastalıkta basit sıklık sıralamasından iyi değil ya da listenin
                 başında doğrulanan ilaç yok)
    ölçülemedi   geriye dönük test yok ya da 3'ten az pozitif örnek var

Çok kesitli benchmark sonucu varsa (evaluation/benchmark.py) tek kesit yerine onun ortalamaları kullanılır.
"""

from __future__ import annotations

from typing import List, Optional

HIGH, MEDIUM, LOW, UNKNOWN = "yüksek", "orta", "düşük", "ölçülemedi"
MIN_POSITIVES = 3


def _ranking_key(metrics: dict) -> str:
    for k in ("fused", "rrf", "method"):
        if k in metrics:
            return k
    return "method"


def grade(backtest: Optional[dict], benchmark_entry: Optional[dict] = None, n_articles: Optional[int] = None,
          external: Optional[dict] = None) -> dict:
    """{"grade", "reasons": [...], "numbers": {...}} döndürür."""
    reasons: List[str] = []
    numbers: dict = {}
    source = None
    ap = rnd = freq = h25 = None
    n_pos = 0

    if benchmark_entry and benchmark_entry.get("groups"):
        groups = benchmark_entry["groups"]
        ap = benchmark_entry.get("mean_chosen_ap") or benchmark_entry.get("mean_base_ap")
        rnd = benchmark_entry.get("mean_random_ap")
        freq = benchmark_entry.get("mean_frequency_ap")
        h25 = benchmark_entry.get("mean_chosen_h25")
        if h25 is None:
            h25 = benchmark_entry.get("mean_base_h25")
        n_pos = min(int(g.get("n_positives") or 0) for g in groups)
        source = f"{len(groups)} kesitli ölçüm ({benchmark_entry.get('scheme')})"
    elif backtest and backtest.get("schemes"):
        scheme = backtest.get("primary_scheme")
        res = (backtest.get("schemes") or {}).get(scheme) or {}
        metrics = res.get("metrics") or {}
        if metrics:
            key = _ranking_key(metrics)
            ap = metrics[key].get("ap")
            freq = (metrics.get("frequency") or {}).get("ap")
            rnd = res.get("random_expected_ap")
            h25 = metrics[key].get("hits_at_25")
            n_pos = int(res.get("n_positives") or 0)
            source = f"{backtest.get('cutoff_year')} kesiti ({scheme})"

    if ap is None or rnd is None or n_pos < MIN_POSITIVES:
        reasons.append("Geriye dönük test yok ya da gelecekte doğrulanan ilaç sayısı ölçüm için çok az.")
        g = UNKNOWN
    else:
        lift = ap / rnd if rnd else None
        numbers = {"ap": round(ap, 4), "random_ap": round(rnd, 4), "frequency_ap": round(freq, 4) if freq is not None else None,
                   "lift_over_random": round(lift, 1) if lift else None, "n_positives": n_pos, "source": source,
                   "hits_at_25": h25}
        beats_freq = freq is None or ap >= freq
        near_freq = freq is None or ap >= 0.8 * freq
        top_hits = h25 is None or h25 >= 0.5
        if lift and lift >= 5 and beats_freq and n_pos >= 5 and top_hits:
            g = HIGH
        elif lift and lift >= 2 and near_freq and top_hits:
            g = MEDIUM
        else:
            g = LOW
        reasons.append(f"{source}: yöntem rastgele sıralamanın {numbers['lift_over_random']} katı isabetli (AP {numbers['ap']}).")
        if freq is not None:
            if ap >= freq:
                reasons.append(f"Basit sıklık sıralamasını geçiyor (AP {numbers['ap']} / {numbers['frequency_ap']}).")
            else:
                reasons.append(f"Basit sıklık sıralamasının gerisinde (AP {numbers['ap']} / {numbers['frequency_ap']}); "
                               "liste daha çok yaygın ilaçları yansıtıyor olabilir.")
        if not top_hits:
            reasons.append("Listenin ilk 25 sırasında, sonradan klinik çalışmaya giren ilaç yok.")
        if n_pos < 5:
            reasons.append(f"Doğrulanan ilaç sayısı az ({n_pos}); ölçüm belirsiz.")

    if n_articles is not None and n_articles < 500:
        reasons.append(f"Hastalık literatürü küçük ({n_articles} makale); köprü genler az.")
        if g == HIGH:
            g = MEDIUM
    ext = external or {}
    meta = ext.get("meta") or {}
    if meta:
        if meta.get("n_genetic"):
            reasons.append(f"Bağımsız genetik kanıt: {meta['n_genetic']} gen (literatürden bağımsız kanal).")
        else:
            reasons.append("Hastalık için genetik ilişki kanıtı bulunamadı; bağımsız kanal zayıf.")
        if meta.get("seed_source") and meta.get("seed_source") != "genetik":
            reasons.append("Ağ kanalı genetik tohum bulamadığı için literatür köprü genleriyle kuruldu (tam bağımsız değil).")
    return {"grade": g, "reasons": reasons, "numbers": numbers}
