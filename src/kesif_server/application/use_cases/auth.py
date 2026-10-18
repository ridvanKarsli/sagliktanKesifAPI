"""Kimlik ve kullanıcı yönetimi senaryoları.

İki kip: 'local' (e-posta + parola, argon2; JWT bu sunucu imzalar) ve 'oidc' (kimlik sağlayıcı — ör. Sağlıktan'ın
mevcut sistemi — token verir; burada doğrulanır, kullanıcı ilk girişte otomatik açılır). İş kuralları ikisinde ortaktır.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from ...domain.entities import Role, TokenClaims, User
from ...domain.errors import AuthenticationError, ConflictError, NotFoundError, ValidationError
from ...domain.ports import Clock, PasswordHasher, TokenService, UnitOfWorkFactory
from ..authorization import require


@dataclass
class TokenPair:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    user: User


# Gerçek bir argon2id özeti (rastgele parola): kullanıcı yokken de doğrulama aynı süreyi harcar, e-posta sızmaz
_DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=2$WESFt46TPbWMb0uF6XbT7A$yrjOAIb88w2rcbEkDbodEnOI5mNgwXX0wk40tJEzMcU"


def _normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 5:
        raise ValidationError("Geçerli bir e-posta adresi girin.")
    return email


def _check_password(password: str) -> None:
    if len(password or "") < 10:
        raise ValidationError("Parola en az 10 karakter olmalı.")


class Login:
    def __init__(self, uow_factory: UnitOfWorkFactory, hasher: PasswordHasher, tokens: TokenService, clock: Clock):
        self._uow, self._hasher, self._tokens, self._clock = uow_factory, hasher, tokens, clock

    def __call__(self, email: str, password: str) -> TokenPair:
        email = _normalize_email(email)
        with self._uow() as uow:
            user = uow.users.get_by_email(email)
            if user is None or user.password_hash is None:
                self._hasher.verify(password, _DUMMY_HASH)  # zamanlama sızıntısını azaltmak için yine de doğrulama yapılır
                raise AuthenticationError("E-posta ya da parola hatalı.")
            if not self._hasher.verify(password, user.password_hash) or not user.is_active:
                raise AuthenticationError("E-posta ya da parola hatalı.")
            user.last_login_at = self._clock.now()
            uow.users.update(user)
            uow.commit()
        return issue_pair(self._tokens, user)


def issue_pair(tokens: TokenService, user: User) -> TokenPair:
    access, a_exp = tokens.issue(user, "access")
    refresh, r_exp = tokens.issue(user, "refresh")
    return TokenPair(access, a_exp, refresh, r_exp, user)


class RefreshTokens:
    def __init__(self, uow_factory: UnitOfWorkFactory, tokens: TokenService):
        self._uow, self._tokens = uow_factory, tokens

    def __call__(self, refresh_token: str) -> TokenPair:
        claims = self._tokens.decode(refresh_token)
        if claims.token_type != "refresh":
            raise AuthenticationError("Yenileme belirteci bekleniyor.")
        with self._uow() as uow:
            user = uow.users.get(claims.subject)
            if user is None or not user.is_active:
                raise AuthenticationError("Kullanıcı bulunamadı ya da pasif.")
        return issue_pair(self._tokens, user)


class ResolveActor:
    """Token'dan kullanıcıya. OIDC'de ilk girişte kullanıcı kaydı otomatik açılır (varsayılan rol: viewer)."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, auto_provision: bool = False):
        self._uow, self._clock, self._auto = uow_factory, clock, auto_provision

    def __call__(self, claims: TokenClaims) -> User:
        if claims.token_type != "access":
            raise AuthenticationError("Erişim belirteci bekleniyor.")
        with self._uow() as uow:
            user = uow.users.get(claims.subject) or uow.users.get_by_subject(claims.subject)
            if user is None and claims.email:
                user = uow.users.get_by_email(claims.email)
                if user and not user.external_subject:
                    user.external_subject = claims.subject
                    uow.users.update(user)
            if user is None and self._auto and claims.email:
                user = uow.users.add(User(email=claims.email, display_name=claims.email.split("@")[0], role=claims.role or Role.VIEWER,
                                          external_subject=claims.subject, created_at=self._clock.now()))
            if user is None or not user.is_active:
                raise AuthenticationError("Kullanıcı bulunamadı ya da pasif.")
            uow.commit()
            return user


class CreateUser:
    def __init__(self, uow_factory: UnitOfWorkFactory, hasher: PasswordHasher, clock: Clock):
        self._uow, self._hasher, self._clock = uow_factory, hasher, clock

    def __call__(self, actor: Optional[User], email: str, password: str, display_name: str, role: Role) -> User:
        require(actor, Role.ADMIN)
        email = _normalize_email(email)
        _check_password(password)
        with self._uow() as uow:
            if uow.users.get_by_email(email):
                raise ConflictError("Bu e-posta zaten kayıtlı.")
            user = uow.users.add(User(email=email, display_name=display_name.strip() or email.split("@")[0], role=role,
                                      password_hash=self._hasher.hash(password), created_at=self._clock.now()))
            uow.commit()
            return user


class BootstrapAdmin:
    """Veritabanında hiç kullanıcı yokken ilk yöneticiyi ortam değişkenlerinden açar (idempotent)."""

    def __init__(self, uow_factory: UnitOfWorkFactory, hasher: PasswordHasher, clock: Clock):
        self._uow, self._hasher, self._clock = uow_factory, hasher, clock

    def __call__(self, email: Optional[str], password: Optional[str]) -> Optional[User]:
        if not email or not password:
            return None
        with self._uow() as uow:
            email = _normalize_email(email)
            if uow.users.get_by_email(email) is not None or any(u.password_hash for u in uow.users.list()):
                return None  # zaten var ya da gerçek (parolalı) kullanıcılar açılmış: dokunma
            _check_password(password)
            user = uow.users.add(User(email=email, display_name="Yönetici", role=Role.ADMIN,
                                      password_hash=self._hasher.hash(password), created_at=self._clock.now()))
            uow.commit()
            return user


class ListUsers:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow = uow_factory

    def __call__(self, actor: User) -> List[User]:
        require(actor, Role.ADMIN)
        with self._uow() as uow:
            return uow.users.list()


class UpdateUser:
    def __init__(self, uow_factory: UnitOfWorkFactory, hasher: PasswordHasher):
        self._uow, self._hasher = uow_factory, hasher

    def __call__(self, actor: User, user_id: str, *, role: Optional[Role] = None, is_active: Optional[bool] = None,
                 display_name: Optional[str] = None, password: Optional[str] = None) -> User:
        require(actor, Role.ADMIN)
        with self._uow() as uow:
            user = uow.users.get(user_id)
            if user is None:
                raise NotFoundError("Kullanıcı bulunamadı.")
            if user.id == actor.id and ((role is not None and role != Role.ADMIN) or is_active is False):
                raise ValidationError("Kendi yönetici yetkinizi kaldıramaz ya da hesabınızı kapatamazsınız.")
            if role is not None:
                user.role = role
            if is_active is not None:
                user.is_active = is_active
            if display_name is not None:
                user.display_name = display_name.strip() or user.display_name
            if password is not None:
                _check_password(password)
                user.password_hash = self._hasher.hash(password)
            uow.users.update(user)
            uow.commit()
            return user


class ChangeOwnPassword:
    def __init__(self, uow_factory: UnitOfWorkFactory, hasher: PasswordHasher):
        self._uow, self._hasher = uow_factory, hasher

    def __call__(self, actor: User, current: str, new: str) -> None:
        require(actor, Role.VIEWER)
        _check_password(new)
        with self._uow() as uow:
            user = uow.users.get(actor.id)
            if user is None or not user.password_hash or not self._hasher.verify(current, user.password_hash):
                raise AuthenticationError("Mevcut parola hatalı.")
            user.password_hash = self._hasher.hash(new)
            uow.users.update(user)
            uow.commit()
