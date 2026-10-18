"""İstek kimliği, erişim günlüğü ve basit hız sınırı (süreç içi; çok kopyada Nginx'teki sınır asıl koruma)."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from typing import Deque, Dict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ...core.logging import request_id_var

log = logging.getLogger("kesif.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        t = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            ms = int((time.perf_counter() - t) * 1000)
            if not request.url.path.endswith("/health"):
                log.info("%s %s -> %s %dms", request.method, request.url.path, response.status_code, ms)
            return response
        finally:
            request_id_var.reset(token)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """İstemci adresi başına dakikalık kayan pencere. Sağlık uçları muaftır."""

    def __init__(self, app, per_minute: int):
        super().__init__(app)
        self._limit = per_minute
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    async def dispatch(self, request: Request, call_next):
        if self._limit <= 0 or request.url.path.endswith("/health"):
            return await call_next(request)
        client = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(client, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self._limit:
                return JSONResponse({"error": {"code": "rate_limited", "message": "Çok fazla istek; biraz sonra yeniden deneyin.", "details": {}}},
                                    status_code=429, headers={"Retry-After": "30"})
            q.append(now)
            if len(self._hits) > 10000:  # bellek güvenliği
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < 60}
        return await call_next(request)
