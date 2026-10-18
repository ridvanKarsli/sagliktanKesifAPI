"""Süreç içi kuyruk: tek işçi iş parçacığı, sıralı çalıştırma. Geliştirme ve testler için; canlıda Redis kullanılır.

Dış veri kaynaklarının hız sınırı ortak olduğundan işler zaten sırayla koşmalıdır.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)


class InProcessQueue:
    def __init__(self, execute: Callable[[str], object], start: bool = True):
        self._execute = execute
        self._q: queue.Queue[str] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="kesif-worker")
        if start:
            self._thread.start()

    def enqueue(self, run_id: str) -> None:
        self._q.put(run_id)

    def ping(self) -> bool:
        return self._thread.is_alive()

    def _loop(self) -> None:
        while True:
            run_id = self._q.get()
            try:
                self._execute(run_id)
            except Exception:
                log.exception("İş %s işçi döngüsünde beklenmedik hata", run_id)
            finally:
                self._q.task_done()

    def drain(self, timeout: Optional[float] = None) -> None:
        """Testler için: kuyruk boşalana kadar bekler."""
        self._q.join()


class SynchronousQueue:
    """Testler için: enqueue anında çalıştırır."""

    def __init__(self, execute: Callable[[str], object]):
        self._execute = execute

    def enqueue(self, run_id: str) -> None:
        self._execute(run_id)

    def ping(self) -> bool:
        return True
