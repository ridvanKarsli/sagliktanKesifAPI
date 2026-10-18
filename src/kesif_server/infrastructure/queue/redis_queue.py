"""Redis/RQ kuyruğu: API süreci işi kuyruğa atar, ayrı `kesif worker` süreçleri çalıştırır.

Birden fazla API kopyası ve bir ya da daha fazla worker ile ölçeklenir; iş durumu her zaman veritabanındadır.
"""

from __future__ import annotations

from redis import Redis
from rq import Queue

EXECUTE_TASK = "kesif_server.infrastructure.queue.tasks.execute_run"


class RedisQueue:
    def __init__(self, redis_url: str, queue_name: str, job_timeout_seconds: int):
        self._redis = Redis.from_url(redis_url)
        self._queue = Queue(queue_name, connection=self._redis, default_timeout=job_timeout_seconds)
        self._timeout = job_timeout_seconds

    def enqueue(self, run_id: str) -> None:
        self._queue.enqueue(EXECUTE_TASK, run_id, job_id=f"run:{run_id}", job_timeout=self._timeout, result_ttl=86400, failure_ttl=7 * 86400)

    def ping(self) -> bool:
        try:
            return bool(self._redis.ping())
        except Exception:  # noqa: BLE001
            return False

    @property
    def connection(self) -> Redis:
        return self._redis

    @property
    def name(self) -> str:
        return self._queue.name
