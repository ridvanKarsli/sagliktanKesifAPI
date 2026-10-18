"""Alan hataları. Arayüz katmanı bunları HTTP durum kodlarına eşler; alan katmanı HTTP bilmez."""

from __future__ import annotations

from typing import Optional


class DomainError(Exception):
    """Tüm alan hatalarının tabanı. `code` makine tarafından okunur, `message` kullanıcıya gösterilir."""

    code = "domain_error"

    def __init__(self, message: str, *, details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(DomainError):
    code = "not_found"


class ConflictError(DomainError):
    code = "conflict"


class ValidationError(DomainError):
    code = "validation_error"


class AuthenticationError(DomainError):
    code = "unauthenticated"


class AuthorizationError(DomainError):
    code = "forbidden"


class ExternalServiceError(DomainError):
    """Dış veri kaynağı (makale/ilaç/klinik çalışma veritabanı) yanıt vermedi."""

    code = "external_service_error"
