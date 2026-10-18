"""Hastalık başına sıralama parametrelerinin kalıcılığı (data/<hastalık>/settings.json)."""

from __future__ import annotations

import json
import logging

from kesif_engine.core.paths import workdir
from kesif_engine.scoring.ranking import Params

log = logging.getLogger("kesif")


# ---------- ayarlar ----------

def load_settings(disease: str) -> Params:
    path = workdir(disease) / "settings.json"
    if path.exists():
        return Params.from_dict(json.loads(path.read_text(encoding="utf-8")))
    return Params()


def save_settings(disease: str, params: Params) -> None:
    (workdir(disease) / "settings.json").write_text(json.dumps(params.to_dict(), indent=1), encoding="utf-8")
