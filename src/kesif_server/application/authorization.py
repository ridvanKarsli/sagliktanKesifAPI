"""Yetki kuralları tek yerde: kullanım senaryoları `require(actor, Role.X)` çağırır."""

from __future__ import annotations

from typing import Optional

from ..domain.entities import Role, User
from ..domain.errors import AuthenticationError, AuthorizationError


def require(actor: Optional[User], role: Role) -> User:
    if actor is None:
        raise AuthenticationError("Giriş yapmanız gerekiyor.")
    if not actor.can(role):
        raise AuthorizationError(f"Bu işlem için '{role.value}' yetkisi gerekiyor.", details={"required": role.value, "actual": actor.role.value})
    return actor
