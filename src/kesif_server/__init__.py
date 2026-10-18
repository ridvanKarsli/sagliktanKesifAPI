"""Sağlıktan Keşif sunucusu — Clean Architecture.

Katmanlar (bağımlılık yönü her zaman içeri doğru):

    interfaces  →  application  →  domain
    infrastructure  →  application/domain  (port'ların somut uygulamaları)

- domain:         varlıklar, değer nesneleri, hata türleri ve port'lar (Protocol). Hiçbir dış bağımlılık yok.
- application:    kullanım senaryoları (use case). Yalnızca domain port'larına bağımlı; FastAPI/SQLAlchemy bilmez.
- infrastructure: PostgreSQL (SQLAlchemy), Redis kuyruk (RQ), kimlik (JWT/argon2/OIDC) ve bilimsel motor adaptörü.
- interfaces:     FastAPI REST API (v1) ve komut satırı.

Bilimsel motor (`kesif_engine`) ayrı bir pakettir; sunucu ona yalnızca `infrastructure.engine` adaptörü üzerinden,
`domain.ports.AnalysisEngine` sözleşmesiyle erişir. Böylece motor değişince API, API değişince motor etkilenmez.
"""

__version__ = "1.0.0"
