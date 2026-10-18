"""Ağ yakınlığı: ilaç hedefleri protein ağında hastalık genlerine ne kadar yakın?

Yöntem: yeniden başlamalı rastgele yürüyüş (RWR; Köhler ve ark. 2008), derece eşlemeli permütasyonla z-skoru.

    p(t+1) = (1 − r) · W · p(t) + r · p0          W: sütun normalize ağırlıklı komşuluk, p0: tohum dağılımı

Ham RWR olasılığı çok bağlantılı "merkez" proteinleri (TP53, UBC...) her hastalıkta öne çıkarır. Bunu düzeltmek için
aynı derece dağılımına sahip rastgele tohum kümeleriyle yürüyüş tekrarlanır; bir düğümün gözlenen olasılığı, aynı
derece kutusundaki düğümlerin rastgele tohumlardaki olasılık dağılımıyla kıyaslanır:
z = (p − ortalama_null) / sapma_null (Guney ve ark. 2016'daki iki yönlü derece eşlemeli örnekleme mantığı).
z ≤ 0 olan düğümler kanıt sayılmaz.

Tohumlar: tercihen hastalığın genetik kanıtlı genleri (literatürden bağımsız). Yoksa literatürdeki birincil köprü
genler kullanılır ve bu durum sonuçta açıkça yazılır (o zaman ağ kanalı literatürden tam bağımsız değildir).

Uygulama numpy ile seyrek çarpım yapar (ek bağımlılık yok); sonuç tohum ve sıralı düğüm listesiyle belirlenir,
rastgele örnekleme sabit tohumla yapılır: aynı girdi her zaman aynı z-skorlarını verir.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

RESTART = 0.5
N_PERMUTATIONS = 200
MAX_ITER = 100
TOL = 1e-10
SEED = 20240611
MIN_BIN = 20   # derece kutusunda en az bu kadar düğüm


class Graph:
    def __init__(self, edges: Iterable[Tuple[str, str, float]]):
        edges = list(edges)
        names = sorted({a for a, _, _ in edges} | {b for _, b, _ in edges})
        self.names: List[str] = names
        self.index: Dict[str, int] = {n: i for i, n in enumerate(names)}
        n = len(names)
        src, dst, w = [], [], []
        for a, b, s in edges:
            i, j = self.index[a], self.index[b]
            src += [i, j]
            dst += [j, i]
            w += [float(s), float(s)]
        self.src = np.array(src, dtype=np.int64)
        self.dst = np.array(dst, dtype=np.int64)
        weight = np.array(w, dtype=float)
        strength = np.bincount(self.src, weights=weight, minlength=n) if n else np.zeros(0)
        # sütun normalizasyonu: kaynak düğümün toplam ağırlığına böl
        self.w = weight / np.where(strength[self.src] > 0, strength[self.src], 1.0) if len(weight) else weight
        self.degree = np.bincount(self.src, minlength=n) if n else np.zeros(0, dtype=np.int64)

    def __len__(self) -> int:
        return len(self.names)

    def propagate(self, p0: np.ndarray, restart: float = RESTART) -> np.ndarray:
        """p0: (n,) ya da (n, k). Yakınsayana kadar RWR."""
        if p0.ndim == 2:
            return np.column_stack([self.propagate(p0[:, k], restart) for k in range(p0.shape[1])])
        n = len(self)
        p = p0.copy()
        for _ in range(MAX_ITER):
            spread = np.bincount(self.dst, weights=self.w * p[self.src], minlength=n)
            new = (1 - restart) * spread + restart * p0
            if np.abs(new - p).max() < TOL:
                return new
            p = new
        return p


def _degree_bins(degree: np.ndarray) -> np.ndarray:
    """Düğümleri dereceye göre kutulara ayırır; her kutuda en az MIN_BIN düğüm (Guney 2016 mantığı)."""
    order = np.argsort(degree, kind="mergesort")
    bins = np.zeros(len(degree), dtype=np.int64)
    b, count, last = 0, 0, None
    for idx in order:
        d = degree[idx]
        if count >= MIN_BIN and d != last:
            b, count = b + 1, 0
        bins[idx] = b
        count += 1
        last = d
    return bins


def proximity(edges: List[Tuple[str, str, float]], seeds: Dict[str, float], n_perm: int = N_PERMUTATIONS,
              restart: float = RESTART, rng_seed: int = SEED) -> Dict[str, float]:
    """Her düğüm için derece eşlemeli z-skoru. seeds: {SEMBOL: ağırlık}; ağda olmayan tohumlar yok sayılır."""
    g = Graph(edges)
    if not len(g):
        return {}
    seed_idx = sorted(g.index[s] for s in seeds if s in g.index)
    if not seed_idx:
        return {}
    weights = np.array([max(float(seeds[g.names[i]]), 1e-6) for i in seed_idx])
    p0 = np.zeros(len(g))
    p0[seed_idx] = weights / weights.sum()
    observed = g.propagate(p0, restart)

    bins = _degree_bins(g.degree)
    members: Dict[int, np.ndarray] = {int(b): np.flatnonzero(bins == b) for b in np.unique(bins)}
    rng = np.random.default_rng(rng_seed)
    null = np.zeros((len(g), n_perm))
    for k in range(n_perm):
        picks = []
        for i in seed_idx:
            pool = members[int(bins[i])]
            picks.append(int(pool[rng.integers(len(pool))]))
        np.add.at(null[:, k], picks, weights / weights.sum())
    null = g.propagate(null, restart)
    # İki yönlü null (Guney 2016): rastgele tohum kümesi VE hedefle aynı derece kutusundaki düğümler. Böylece çok
    # bağlantılı bir protein, yalnızca kendisi gibi çok bağlantılı proteinlerle kıyaslanır.
    z = np.zeros(len(g))
    for b, idx in members.items():
        pool = null[idx].ravel()
        mu, sd = pool.mean(), pool.std()
        z[idx] = (observed[idx] - mu) / sd if sd > 0 else 0.0
    return {g.names[i]: round(float(z[i]), 4) for i in range(len(g))}


def choose_seeds(genetic: Optional[dict], bridge_symbols: Iterable[str], min_genetic: float = 0.1,
                 max_seeds: int = 150) -> Tuple[Dict[str, float], str]:
    """Tohum seçimi: genetik kanıtı ≥ min_genetic olan genler; en az 5 tane yoksa literatür köprü genleri."""
    gen = {}
    for sym, sc in (genetic or {}).items():
        v = max(float(sc.get("genetic_association", 0) or 0), float(sc.get("somatic_mutation", 0) or 0))
        if v >= min_genetic:
            gen[str(sym).upper()] = v
    if len(gen) >= 5:
        top = sorted(gen.items(), key=lambda kv: (-kv[1], kv[0]))[:max_seeds]
        return dict(top), "genetik"
    syms = sorted({s.upper() for s in bridge_symbols if s})
    return {s: 1.0 for s in syms[:max_seeds]}, "literatür köprü genleri"
