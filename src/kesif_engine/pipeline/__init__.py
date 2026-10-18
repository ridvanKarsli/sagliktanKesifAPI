"""Analiz hattı: motorun dışarıya açık kapısı. Sunucu ve komut satırı motoru yalnızca bu paket üzerinden kullanır.

    settings   hastalık parametrelerinin kalıcılığı
    status     adım durumları
    corpus     derlem kurma (Corpus)
    steps      adımlar ve sıralı çalıştırıcı (run_steps)
    results    çıktıları okuma
"""

from kesif_engine.core.paths import resultsdir, workdir
from kesif_engine.pipeline.corpus import Corpus, merge_orthologs
from kesif_engine.pipeline.results import load_backtest, load_candidates, load_evidence, load_known
from kesif_engine.pipeline.settings import load_settings, save_settings
from kesif_engine.pipeline.status import status
from kesif_engine.pipeline.steps import (
    DEFAULT_STEPS,
    MAX_ARTICLES_DEFAULT,
    STEP_ORDER,
    estimate,
    run_steps,
    step_backtest,
    step_discovery,
    step_drugs,
    step_fetch,
    step_labels,
    step_novelty,
    step_rank,
    step_report,
    step_tune,
)

__all__ = [
    "DEFAULT_STEPS", "MAX_ARTICLES_DEFAULT", "STEP_ORDER", "Corpus", "estimate", "load_backtest", "load_candidates",
    "load_evidence", "load_known", "load_settings", "merge_orthologs", "resultsdir", "run_steps", "save_settings", "status",
    "step_backtest", "step_discovery", "step_drugs", "step_fetch", "step_labels", "step_novelty", "step_rank", "step_report",
    "step_tune", "workdir",
]
