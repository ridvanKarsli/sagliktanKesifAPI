"""Motorun çalışma dizini düzeni: data/<hastalık>/ (girdiler, önbellek) ve results/<hastalık>/ (çıktılar)."""

from __future__ import annotations

from pathlib import Path

from kesif_engine.core import config


def workdir(disease: str) -> Path:
    path = config.DATA_DIR / disease
    path.mkdir(parents=True, exist_ok=True)
    return path


def resultsdir(disease: str) -> Path:
    path = config.RESULTS_DIR / disease
    path.mkdir(parents=True, exist_ok=True)
    return path
