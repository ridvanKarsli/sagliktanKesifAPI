"""Belirteç servisleri.

- LocalJwtTokenService: bu sunucunun imzaladığı HS256 erişim/yenileme belirteçleri.
- OidcTokenService: dış kimlik sağlayıcının (Keycloak, Auth0, Sağlıktan SSO…) RS256 belirteçlerini JWKS ile doğrular.
  Rol, belirteçteki `roles`/`realm_access.roles` alanından okunur; yoksa viewer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import jwt

from ...domain.entities import Role, TokenClaims, User
from ...domain.errors import AuthenticationError


class LocalJwtTokenService:
    def __init__(self, secret: str, issuer: str, access_ttl_minutes: int, refresh_ttl_days: int):
        self._secret, self._issuer = secret, issuer
        self._ttl = {"access": timedelta(minutes=access_ttl_minutes), "refresh": timedelta(days=refresh_ttl_days)}

    def issue(self, user: User, token_type: str = "access") -> Tuple[str, datetime]:
        now = datetime.now(timezone.utc)
        exp = now + self._ttl[token_type]
        payload = {"sub": user.id, "email": user.email, "role": user.role.value, "typ": token_type,
                   "iss": self._issuer, "iat": int(now.timestamp()), "exp": int(exp.timestamp())}
        return jwt.encode(payload, self._secret, algorithm="HS256"), exp

    def decode(self, token: str) -> TokenClaims:
        try:
            data = jwt.decode(token, self._secret, algorithms=["HS256"], issuer=self._issuer, options={"require": ["exp", "sub"]})
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Oturum süresi doldu.") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("Geçersiz belirteç.") from exc
        return TokenClaims(subject=data["sub"], email=data.get("email"), role=Role(data["role"]) if data.get("role") else None,
                           token_type=data.get("typ", "access"), expires_at=datetime.fromtimestamp(data["exp"], tz=timezone.utc))


class OidcTokenService:
    ROLE_MAP = {"kesif-admin": Role.ADMIN, "kesif-analyst": Role.ANALYST, "admin": Role.ADMIN, "analyst": Role.ANALYST}

    def __init__(self, issuer: str, audience: Optional[str], jwks_url: Optional[str]):
        self._issuer, self._audience = issuer, audience
        self._jwks = jwt.PyJWKClient(jwks_url or f"{issuer.rstrip('/')}/protocol/openid-connect/certs", cache_keys=True)

    def issue(self, user: User, token_type: str = "access") -> Tuple[str, datetime]:
        raise AuthenticationError("OIDC kipinde belirteçleri kimlik sağlayıcı verir.")

    def decode(self, token: str) -> TokenClaims:
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            options = {"require": ["exp", "sub"], "verify_aud": bool(self._audience)}
            data = jwt.decode(token, key, algorithms=["RS256", "ES256"], issuer=self._issuer, audience=self._audience, options=options)
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Oturum süresi doldu.") from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"Geçersiz belirteç: {exc}") from exc
        roles = set(data.get("roles") or []) | set((data.get("realm_access") or {}).get("roles") or [])
        role = max((self.ROLE_MAP[r] for r in roles if r in self.ROLE_MAP), key=lambda r: r.level, default=Role.VIEWER)
        return TokenClaims(subject=data["sub"], email=data.get("email"), role=role, token_type="access",
                           expires_at=datetime.fromtimestamp(data["exp"], tz=timezone.utc))
