from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, status

from .....container import Container
from .....domain.entities import Role, User
from .....domain.errors import ConflictError
from ...deps import current_user, get_container, require_role
from ...schemas.auth import LoginIn, PasswordChangeIn, RefreshIn, TokenOut, UserCreateIn, UserOut, UserUpdateIn
from ...schemas.common import OkResponse

router = APIRouter(tags=["kimlik"])


def _token_out(pair) -> TokenOut:
    return TokenOut(access_token=pair.access_token, access_expires_at=pair.access_expires_at, refresh_token=pair.refresh_token,
                    refresh_expires_at=pair.refresh_expires_at, user=UserOut.from_entity(pair.user))


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, c: Container = Depends(get_container)):
    if c.settings.auth_mode != "local":
        raise ConflictError("Bu kurulumda giriş kimlik sağlayıcı üzerinden yapılır.")
    return _token_out(c.login(body.email, body.password))


@router.post("/auth/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, c: Container = Depends(get_container)):
    return _token_out(c.refresh_tokens(body.refresh_token))


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return UserOut.from_entity(user)


@router.post("/auth/password", response_model=OkResponse)
def change_password(body: PasswordChangeIn, user: User = Depends(current_user), c: Container = Depends(get_container)):
    c.change_own_password(user, body.current_password, body.new_password)
    return OkResponse()


@router.get("/users", response_model=List[UserOut])
def list_users(user: User = Depends(require_role(Role.ADMIN)), c: Container = Depends(get_container)):
    return [UserOut.from_entity(u) for u in c.list_users(user)]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreateIn, user: User = Depends(require_role(Role.ADMIN)), c: Container = Depends(get_container)):
    return UserOut.from_entity(c.create_user(user, body.email, body.password, body.display_name, body.role))


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserUpdateIn, user: User = Depends(require_role(Role.ADMIN)), c: Container = Depends(get_container)):
    return UserOut.from_entity(c.update_user(user, user_id, role=body.role, is_active=body.is_active, display_name=body.display_name,
                                             password=body.password))
