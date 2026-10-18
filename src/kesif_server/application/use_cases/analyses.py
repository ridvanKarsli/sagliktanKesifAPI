"""Analiz işleri: başlatma, iptal, sorgulama, worker tarafında çalıştırma, hızlı yeniden sıralama, parametreler."""

from __future__ import annotations

import logging
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...domain.entities import AnalysisRun, AnalysisStep, Role, RunOptions, RunProgress, RunStatus, User
from ...domain.errors import ConflictError, NotFoundError, ValidationError
from ...domain.ports import AnalysisEngine, Clock, JobQueue, UnitOfWorkFactory, WorkspaceSync
from ..authorization import require
from .candidates import SyncResults
from .diseases import _get_or_404

log = logging.getLogger(__name__)


class StartAnalysis:
    def __init__(self, uow_factory: UnitOfWorkFactory, engine: AnalysisEngine, queue: JobQueue, clock: Clock):
        self._uow, self._engine, self._queue, self._clock = uow_factory, engine, queue, clock

    def __call__(self, actor: User, disease_key: str, steps: List[str], params: Optional[Dict[str, Any]] = None,
                 options: Optional[Dict[str, Any]] = None) -> AnalysisRun:
        require(actor, Role.ANALYST)
        try:
            ordered = AnalysisStep.ordered(steps)
        except ValueError as exc:
            raise ValidationError(f"Bilinmeyen adım: {exc}") from exc
        if not ordered:
            raise ValidationError("En az bir adım seçin.")
        with self._uow() as uow:
            disease = _get_or_404(uow, disease_key)
            p = self._engine.validate_params(params if params is not None else (disease.params or {}))
            if uow.runs.active_for(disease_key):
                raise ConflictError("Bu hastalık için zaten sırada ya da çalışan bir analiz var.")
            run = AnalysisRun(disease_key=disease_key, steps=ordered, params=p, options=RunOptions.from_dict(options or {}),
                              created_by=actor.id, created_at=self._clock.now())
            run.queue_position = len(uow.runs.queued()) + 1
            uow.runs.add(run)
            uow.commit()
        self._queue.enqueue(run.id)
        return run


class CancelAnalysis:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock):
        self._uow, self._clock = uow_factory, clock

    def __call__(self, actor: User, run_id: str) -> AnalysisRun:
        require(actor, Role.ANALYST)
        with self._uow() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise NotFoundError("İş bulunamadı.")
            if run.status != RunStatus.QUEUED:
                raise ConflictError("Yalnızca sırada bekleyen işler iptal edilebilir.")
            run.mark_cancelled(self._clock.now())
            uow.runs.update(run)
            uow.commit()
            return run


class GetRun:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow = uow_factory

    def __call__(self, run_id: str) -> AnalysisRun:
        with self._uow() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise NotFoundError("İş bulunamadı.")
            return run


class ListRuns:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow = uow_factory

    def __call__(self, disease_key: Optional[str] = None, limit: int = 50) -> List[AnalysisRun]:
        with self._uow() as uow:
            return uow.runs.list(disease_key, limit)


class ExecuteRun:
    """Worker tarafı: kuyruktan gelen işi çalıştırır, ilerlemeyi veritabanına yazar, bitince adayları içe aktarır.

    Ağ kaynaklı hatalarda bir kez bekleyip yeniden dener (indirilenler önbellekte olduğundan kaldığı yerden sürer).
    Kod hatasında hemen 'failed' olur. Her durumda son durum veritabanındadır; API süreci yeniden başlasa da kaybolmaz.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, engine: AnalysisEngine, clock: Clock, results_dir: Path,
                 sync: SyncResults, workspace: WorkspaceSync, retry_delay_seconds: int = 60, max_attempts: int = 2):
        self._uow, self._engine, self._clock = uow_factory, engine, clock
        self._results_dir, self._sync, self._ws = results_dir, sync, workspace
        self._retry_delay, self._max_attempts = retry_delay_seconds, max_attempts

    def _save_progress(self, run_id: str, progress: RunProgress) -> None:
        with self._uow() as uow:
            run = uow.runs.get(run_id)
            if run:
                run.progress = progress
                uow.runs.update(run)
                uow.commit()

    def _finish(self, run_id: str, disease_key: str, log_path: Path, done: bool, summary: Optional[Dict[str, Any]] = None,
                error: Optional[str] = None) -> AnalysisRun:
        # Başarılı ya da başarısız: üretilmiş olan her şey (indirilen derlem dahil) veritabanına alınır
        imported: Dict[str, Any]
        try:
            imported = dict(self._sync(disease_key, run_id))
            imported["files"] = self._ws.save(disease_key)
        except Exception as exc:
            log.exception("Sonuçlar veritabanına aktarılamadı")
            imported = {"error": f"{type(exc).__name__}: {exc}"}
        log_text = _tail(log_path)
        with self._uow() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise NotFoundError(f"İş bulunamadı: {run_id}")
            if done:
                run.mark_done(self._clock.now(), {**(summary or {}), "imported": imported})
            else:
                run.mark_failed(self._clock.now(), error or "bilinmeyen hata")
            run.log = log_text
            uow.runs.update(run)
            uow.commit()
            return run

    def __call__(self, run_id: str) -> AnalysisRun:
        with self._uow() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise NotFoundError(f"İş bulunamadı: {run_id}")
            if run.status != RunStatus.QUEUED:
                log.info("İş %s '%s' durumunda; atlanıyor.", run_id, run.status.value)
                return run
            disease = _get_or_404(uow, run.disease_key)
            run.mark_running(self._clock.now())
            uow.runs.update(run)
            uow.commit()

        log_path = self._results_dir / run.disease_key / "job.log"
        try:
            self._ws.prepare(run.disease_key)  # önbellek ve önceki sonuçlar veritabanından çalışma dizinine
        except Exception as exc:  # noqa: BLE001
            return self._finish(run_id, run.disease_key, log_path, done=False, error=f"Çalışma dizini hazırlanamadı: {exc}")
        steps = [s.value for s in run.steps]
        options = run.options.to_dict()

        def on_progress(p: RunProgress) -> None:
            self._save_progress(run_id, p)

        attempt = 0
        while True:
            attempt += 1
            try:
                summary = self._engine.run(disease, steps, run.params, options, progress=on_progress, log_path=log_path)
                return self._finish(run_id, run.disease_key, log_path, done=True, summary=summary)
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                if attempt < self._max_attempts and self._engine.is_network_error(exc):
                    log.warning("Ağ hatası (%s); %d sn sonra yeniden denenecek.", error, self._retry_delay)
                    time.sleep(self._retry_delay)
                    continue
                log.error("İş %s başarısız: %s\n%s", run_id, error, traceback.format_exc())
                return self._finish(run_id, run.disease_key, log_path, done=False, error=error)


class Rerank:
    """Parametrelerle hızlı yeniden sıralama (veri indirmez); isteğe bağlı backtest; sonuçlar veritabanına yazılır."""

    def __init__(self, uow_factory: UnitOfWorkFactory, engine: AnalysisEngine, sync: SyncResults, workspace: WorkspaceSync):
        self._uow, self._engine, self._sync, self._ws = uow_factory, engine, sync, workspace

    def __call__(self, actor: User, disease_key: str, params: Dict[str, Any], cutoff: Optional[int] = None) -> Dict[str, Any]:
        require(actor, Role.ANALYST)
        p = self._engine.validate_params(params)
        with self._uow() as uow:
            disease = _get_or_404(uow, disease_key)
            if uow.runs.active_for(disease_key):
                raise ConflictError("Bu hastalık için çalışan bir iş var; bitmesini bekleyin.")
            disease.params = p
            uow.diseases.update(disease)
            uow.commit()
        self._ws.prepare(disease_key)
        result = self._engine.rerank(disease, p, cutoff)
        result["imported"] = self._sync(disease_key, None)
        self._ws.save(disease_key)
        return result


class GetSettings:
    def __init__(self, uow_factory: UnitOfWorkFactory, engine: AnalysisEngine):
        self._uow, self._engine = uow_factory, engine

    def __call__(self, disease_key: str) -> Dict[str, Any]:
        defaults = self._engine.default_params()
        with self._uow() as uow:
            d = _get_or_404(uow, disease_key)
        return {"params": {**defaults, **(d.params or {})}, "defaults": defaults}


class UpdateSettings:
    def __init__(self, uow_factory: UnitOfWorkFactory, engine: AnalysisEngine):
        self._uow, self._engine = uow_factory, engine

    def __call__(self, actor: User, disease_key: str, params: Dict[str, Any]) -> Dict[str, Any]:
        require(actor, Role.ANALYST)
        p = self._engine.validate_params(params)
        with self._uow() as uow:
            d = _get_or_404(uow, disease_key)
            d.params = p
            uow.diseases.update(d)
            uow.commit()
        return p


def _tail(path: Path, lines: int = 2000) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return "\n".join(text.splitlines()[-lines:])
