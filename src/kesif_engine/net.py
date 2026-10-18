from __future__ import annotations

import time
from urllib.parse import urlparse

import requests

from . import config


class HttpClient:
    """Hız sınırı ve yeniden deneme içeren basit HTTP istemcisi."""

    def __init__(self, timeout: int = 60, retries: int = 4):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "kesif/0.1 (arastirma prototipi)"
        self.timeout = timeout
        self.retries = retries
        self._last: dict = {}

    def _gap(self, host: str) -> float:
        if host.endswith("ncbi.nlm.nih.gov"):
            return 0.11 if config.NCBI_API_KEY else 0.36
        return 0.3

    def _wait(self, host: str) -> None:
        gap = self._gap(host)
        last = self._last.get(host, 0.0)
        now = time.monotonic()
        if now - last < gap:
            time.sleep(gap - (now - last))
        self._last[host] = time.monotonic()

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        host = urlparse(url).hostname or ""
        last_err = None
        for attempt in range(self.retries):
            self._wait(host)
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                last_err = exc
            else:
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_err = RuntimeError(f"HTTP {resp.status_code} ({host})")
                elif resp.status_code in (403, 407):
                    # Erişim engeli yeniden denemeyle düzelmez.
                    raise PermissionError(f"{host} erişimi reddetti (HTTP {resp.status_code})")
                else:
                    resp.raise_for_status()
                    return resp
            time.sleep(2 ** attempt)
        raise RuntimeError(f"İstek başarısız oldu: {url} ({last_err})")
