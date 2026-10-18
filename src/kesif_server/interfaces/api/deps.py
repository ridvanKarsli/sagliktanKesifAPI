"""FastAPI bağımlılıkları: bileşim kökü, mevcut kullanıcı, rol denetimi."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ...container import Container
from ...domain.entities import Role, User
from ...domain.errors import AuthenticationError, AuthorizationError

bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    return request.app.state.container


def current_user(request: Request, creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
                 container: Container = Depends(get_container)) -> User:
    if container.settings.auth_mode == "disabled":
        user = container.local_actor()
        if user is None:
            raise AuthenticationError("Yerel kullanıcı hazır değil.")
        request.state.user = user
        return user
    if creds is None or not creds.credentials:
        raise AuthenticationError("Giriş yapmanız gerekiyor.")
    claims = container.tokens.decode(creds.credentials)
    user = container.resolve_actor(claims)
    request.state.user = user
    return user


def require_role(role: Role):
    def _dep(user: User = Depends(current_user)) -> User:
        if not user.can(role):
            raise AuthorizationError(f"Bu işlem için '{role.value}' yetkisi gerekiyor.")
        return user

    return _dep
