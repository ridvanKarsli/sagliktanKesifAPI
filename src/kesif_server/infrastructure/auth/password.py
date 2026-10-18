"""Parola özetleme: argon2id (OWASP önerisi)."""

from __future__ import annotations

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import VerificationError, VerifyMismatchError


class Argon2PasswordHasher:
    def __init__(self):
        self._h = _Argon2(time_cost=3, memory_cost=64 * 1024, parallelism=2)

    def hash(self, password: str) -> str:
        return self._h.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            return self._h.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, ValueError):
            return False
