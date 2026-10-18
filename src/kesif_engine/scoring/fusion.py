"""Kanıt kanallarının tek sıralamada birleştirilmesi.

İki yöntem:

  rrf       Karşılıklı sıra birleştirme (Cormack ve ark. 2009): skor = Σ_kanal w / (k + sıra). Parametresiz, ölçekten
            bağımsız ve sağlam; eğitim verisi gerektirmez. Varsayılan birleştirme budur.

  logistic  Geçmiş kesitlerden öğrenen lojistik model. Girdiler her hastalığın kendi içinde yüzdelik sıraya çevrilir
            (küçük ve büyük literatürlü hastalıklar aynı ölçekte olur). Kanıt kanallarının katsayısı negatif olamaz:
            "daha çok kanıt puanı düşürmez" kuralı modele kısıt olarak konur; yalnızca yaygınlık (prevalence) gibi
            denetim sütunları negatif ağırlık alabilir. Katsayılar görünürdür; aynı veri her zaman aynı sonucu verir.

Hangi yöntemin kullanılacağına ölçüm karar verir: öğrenen model, eğitimde görmediği hastalıklarda RRF'yi geçmedikçe
kullanılmaz (evaluation/benchmark.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from kesif_engine.core import config
from kesif_engine.scoring.features import CHANNELS, available_channels

available = available_channels  # benchmark ve backtest için kısa ad
RRF_K = 60
SIGNED_FEATURES = ("prevalence",)  # bu sütunlar negatif katsayı alabilir


def percentile(series: pd.Series) -> pd.Series:
    """0 olan değerler 0 kalır; pozitifler kendi aralarında (0, 1] yüzdelik sıraya çevrilir."""
    out = pd.Series(0.0, index=series.index)
    pos = series[series > 0]
    if len(pos):
        out[pos.index] = pos.rank(method="average", pct=True)
    return out


def rrf(features: pd.DataFrame, channels: Iterable[str], weights: Optional[Dict[str, float]] = None, k: int = RRF_K) -> pd.Series:
    """Her kanalda sıfırdan büyük değer alan ilaçlar için Σ w / (k + sıra)."""
    weights = weights or {}
    score = pd.Series(0.0, index=features.index)
    for ch in channels:
        if ch not in features.columns:
            continue
        col = features[ch]
        pos = col[col > 0]
        if pos.empty:
            continue
        ranks = pos.rank(method="min", ascending=False)
        score[pos.index] += weights.get(ch, 1.0) / (k + ranks)
    return score


def transform(features: pd.DataFrame, names: Iterable[str]) -> np.ndarray:
    cols = []
    for n in names:
        if n == "prevalence":
            cols.append(features[n].rank(pct=True).to_numpy() if len(features) else np.zeros(0))
        else:
            cols.append(percentile(features[n]).to_numpy())
    return np.column_stack(cols) if cols else np.zeros((len(features), 0))


class LogisticFusion:
    """Kısıtlı (kanıt katsayıları ≥ 0), L2 düzenlemeli, sınıf ağırlıklı lojistik regresyon (numpy; bağımlılık yok)."""

    def __init__(self, names: List[str], l2: float = 1.0, max_iter: int = 3000, lr: float = 0.5):
        self.names = list(names)
        self.l2, self.max_iter, self.lr = l2, max_iter, lr
        self.coef = np.zeros(len(self.names))
        self.intercept = 0.0
        self.meta: dict = {}

    def fit(self, groups: List[pd.DataFrame], labels: List[pd.Series]) -> LogisticFusion:
        """groups: hastalık×kesit başına özellik tablosu; labels: aynı indeksli 0/1 pozitif etiketleri."""
        xs, ys = [], []
        for f, y in zip(groups, labels):
            if len(f) == 0:
                continue
            xs.append(transform(f, self.names))
            ys.append(y.reindex(f.index).fillna(0).astype(float).to_numpy())
        x, y = np.vstack(xs), np.concatenate(ys)
        n_pos = y.sum()
        if n_pos == 0 or n_pos == len(y):
            raise ValueError("Eğitim için hem pozitif hem negatif örnek gerekir.")
        w = np.where(y > 0, 0.5 / n_pos, 0.5 / (len(y) - n_pos))  # sınıf dengeli ağırlık, toplam 1
        signed = np.array([n in SIGNED_FEATURES for n in self.names])
        b, c = np.zeros(x.shape[1]), 0.0
        for _ in range(self.max_iter):
            p = 1.0 / (1.0 + np.exp(-(x @ b + c)))
            g = p - y
            grad_b = x.T @ (w * g) + self.l2 * b / len(y)
            grad_c = float((w * g).sum())
            b_new = b - self.lr * grad_b * 10
            c_new = c - self.lr * grad_c * 10
            b_new = np.where(signed, b_new, np.maximum(b_new, 0.0))  # kanıt kanalları negatif olamaz
            if np.max(np.abs(b_new - b)) < 1e-7 and abs(c_new - c) < 1e-7:
                b, c = b_new, c_new
                break
            b, c = b_new, c_new
        self.coef, self.intercept = b, c
        self.meta = {"n_rows": int(len(y)), "n_positive": int(n_pos), "n_groups": len(xs)}
        return self

    def predict(self, features: pd.DataFrame) -> pd.Series:
        if len(features) == 0:
            return pd.Series(dtype=float)
        x = transform(features, self.names)
        z = x @ self.coef + self.intercept
        return pd.Series(1.0 / (1.0 + np.exp(-z)), index=features.index)

    def to_dict(self) -> dict:
        return {"type": "logistic", "features": self.names, "coef": [round(float(v), 6) for v in self.coef],
                "intercept": round(float(self.intercept), 6), "l2": self.l2, "transform": "hastalık içi yüzdelik sıra",
                **self.meta}

    @classmethod
    def from_dict(cls, d: dict) -> LogisticFusion:
        m = cls(d["features"], l2=d.get("l2", 1.0))
        m.coef = np.array(d["coef"], dtype=float)
        m.intercept = float(d["intercept"])
        m.meta = {k: d[k] for k in ("n_rows", "n_positive", "n_groups") if k in d}
        return m

    def explain(self) -> List[dict]:
        return sorted(({"feature": n, "weight": round(float(c), 4)} for n, c in zip(self.names, self.coef)), key=lambda r: -abs(r["weight"]))


# ---------------- uygulama ----------------

def model_path(data_dir: Path) -> Path:
    return Path(data_dir) / "benchmark" / "fusion_model.json"


def load_model(data_dir: Path) -> Optional[dict]:
    """Bu kurulumda benchmark'ın verdiği birleştirme kararı: {"method": "logistic"|"rrf"|"base", ...}. Yoksa None."""
    path = model_path(data_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def default_decision() -> dict:
    """Paketle gelen, geliştirme sırasında ölçülmüş karar (resources/fusion_default.json)."""
    path = config.PACKAGE_DIR / "resources" / "fusion_default.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"method": "base"}


def current_decision(data_dir: Optional[Path] = None) -> dict:
    """Kullanılacak karar: kurulumun kendi ölçümü varsa o, yoksa paketle gelen ölçülmüş karar."""
    own = load_model(Path(data_dir) if data_dir else config.DATA_DIR)
    if own:
        return {**own, "origin": "bu kurulumun ölçümü"}
    return {**default_decision(), "origin": "paketle gelen ölçüm"}


def fused_score(features: pd.DataFrame, decision: Optional[dict]) -> pd.Series:
    """Son puan. Karar yoksa ya da 'base' ise mevcut yöntem puanı (değişiklik ölçümle kanıtlanmadıkça uygulanmaz)."""
    if len(features) == 0:
        return pd.Series(dtype=float)
    decision = decision or {}
    method = decision.get("method", "base")
    if method == "logistic" and decision.get("model"):
        s = LogisticFusion.from_dict(decision["model"]).predict(features)
    elif method == "rrf":
        s = rrf(features, decision.get("channels") or CHANNELS, decision.get("weights"))
    else:
        return features["base"].astype(float)
    return s * features["penalty"].astype(float)


def apply_to_candidates(ranked: pd.DataFrame, features: pd.DataFrame, decision: Optional[dict], drug_map: Optional[dict] = None,
                        use_dict: bool = True) -> pd.DataFrame:
    """Aday tablosuna kanal sütunlarını ekler ve karar 'base' değilse son puanı birleşik puanla değiştirir.

    Ek sütunlar: method_score (mevcut yöntem puanı, her zaman korunur), direct_score, recent_score, genetic_score,
    biology_score, network_score, n_channels (sıfırdan büyük kanıt kanalı sayısı), fusion (kullanılan yöntem).
    Sıra numarası yeni puana göre yeniden verilir; eşitlikte eski sıra korunur (kararlı).
    """
    out = ranked.copy()
    if not len(out):
        for col in ("method_score", "direct_score", "recent_score", "genetic_score", "biology_score", "network_score", "n_channels", "fusion"):
            out[col] = []
        return out
    drug_map = drug_map or {}
    keys = [drug_map.get(c, c) if use_dict else c for c in out["chemical_id"]]
    f = features.reindex(keys).fillna(0.0)
    method = (decision or {}).get("method", "base")
    fused = fused_score(features, decision).reindex(keys).fillna(0.0)
    out["method_score"] = out["score"]
    for ch in ("direct", "recent", "genetic", "biology", "network"):
        out[f"{ch}_score"] = f[ch].round(4).to_numpy()
    out["n_channels"] = (f[list(c for c in CHANNELS if c in f.columns)] > 0).sum(axis=1).astype(int).to_numpy()
    out["fusion"] = method
    if method != "base":
        out["score"] = fused.round(6).to_numpy()
        out = out.reset_index(drop=True)
        out = out.sort_values(["score", "method_score", "chemical", "chemical_id"], ascending=[False, False, True, True], kind="mergesort")
        out = out.reset_index(drop=True)
        out.index = out.index + 1
        out.index.name = "rank"
    return out
