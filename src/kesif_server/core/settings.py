"""Ortam değişkenlerinden okunan, doğrulanmış uygulama ayarları (12-factor).

Tüm değişkenler `KESIF_` öneki ile verilir; örnekler `.env.example` dosyasında.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from typing_extensions import Annotated


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KESIF_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    app_name: str = "Sağlıktan Keşif"
    log_level: str = "INFO"
    log_json: bool = False

    # --- veri ---
    database_url: str = "sqlite:///./kesif.db"
    db_echo: bool = False
    # Motorun geçici çalışma dizini. Kalıcı veri değildir: her çalıştırma öncesi veritabanından doldurulur, sonrası
    # veritabanına yazılır; silinebilir. Kod deposunun dışında tutulur.
    work_dir: Path = Path.home() / ".cache" / "kesif"

    # --- kuyruk ---
    queue_backend: Literal["inprocess", "redis"] = "inprocess"
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "kesif"
    job_timeout_seconds: int = 6 * 3600
    job_retry_delay_seconds: int = 60

    # --- kimlik ---
    auth_mode: Literal["local", "oidc", "disabled"] = "local"
    jwt_secret: str = "change-me-in-production-please-32-chars-min"
    jwt_issuer: str = "kesif"
    jwt_access_ttl_minutes: int = 60
    jwt_refresh_ttl_days: int = 14
    oidc_issuer: Optional[str] = None       # ör. https://auth.sagliktan.com/realms/sagliktan
    oidc_audience: Optional[str] = None
    oidc_jwks_url: Optional[str] = None
    bootstrap_admin_email: Optional[str] = None
    bootstrap_admin_password: Optional[str] = None

    # --- HTTP ---
    # NoDecode: ortam değişkeni JSON değil, virgülle ayrılmış liste ("http://a,http://b")
    cors_origins: Annotated[List[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    api_prefix: str = "/api/v1"
    docs_enabled: bool = True
    rate_limit_per_minute: int = 300

    # --- dış servisler ---
    ncbi_api_key: Optional[str] = None
    ncbi_email: Optional[str] = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):  # JSON listesi de kabul edilir
                import json

                return json.loads(v)
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    def validate_for_production(self) -> None:
        """Canlıda tehlikeli varsayılanlarla açılmayı engeller."""
        problems = []
        if self.jwt_secret.startswith("change-me-in-production") and self.auth_mode == "local":
            problems.append("KESIF_JWT_SECRET varsayılan değerde")
        if self.database_url.startswith("sqlite"):
            problems.append("canlıda SQLite kullanılamaz (KESIF_DATABASE_URL)")
        if self.queue_backend != "redis":
            problems.append("canlıda kuyruk redis olmalı (KESIF_QUEUE_BACKEND=redis)")
        if self.auth_mode == "disabled":
            problems.append("canlıda kimlik doğrulama kapatılamaz")
        if problems:
            raise RuntimeError("Canlı yapılandırma hataları: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_prod:
        s.validate_for_production()
    return s
