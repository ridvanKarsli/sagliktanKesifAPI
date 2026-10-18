"""Zaman kaynağı soyutlaması: testlerde sabitlenebilir, üretimde UTC."""

from __future__ import annotations

from datetime import datetime, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    def __init__(self, at: datetime):
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, **kwargs) -> None:
        from datetime import timedelta

        self._at = self._at + timedelta(**kwargs)
