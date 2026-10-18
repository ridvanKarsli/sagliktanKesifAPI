from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from ....domain.entities import Role, User


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshIn(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: Role
    is_active: bool
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None

    @classmethod
    def from_entity(cls, u: User) -> UserOut:
        return cls(id=u.id, email=u.email, display_name=u.display_name, role=u.role, is_active=u.is_active,
                   created_at=u.created_at, last_login_at=u.last_login_at)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    user: UserOut


class UserCreateIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(default="", max_length=200)
    role: Role = Role.VIEWER


class UserUpdateIn(BaseModel):
    role: Optional[Role] = None
    is_active: Optional[bool] = None
    display_name: Optional[str] = Field(default=None, max_length=200)
    password: Optional[str] = Field(default=None, min_length=10, max_length=200)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)
