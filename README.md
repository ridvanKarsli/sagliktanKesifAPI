# Sağlıktan Keşif — Backend

Literatür tabanlı ilaç yeniden konumlandırma platformunun sunucu tarafı. Bir hastalık için makale derlemi toplanır, köprü
genler ve ilaç ilişkileri çıkarılır, adaylar puanlanır, geriye dönük doğrulanır ve bağımsız kaynaklarla yenilik kontrolü
yapılır. Bu repo bilimsel motoru (`kesif_engine`) ve Clean Architecture ile kurulmuş REST API'yi (`kesif_server`)
içerir. Arayüz ayrı repodadır (Sağlıktan Keşif — Frontend) ve bu API'ye yalnızca `/api/v1` üzerinden bağlanır.

## Sistem

```
┌──────────────┐   HTTPS    ┌──────────────┐  SQL   ┌──────────────────────────────────────────────┐
│  frontend    │ ─────────▶ │  api (×N)    │ ─────▶ │ PostgreSQL                                   │
│  (ayrı repo) │ ◀───────── │  FastAPI     │        │ kullanıcı · hastalık · iş · aday · çıktılar  │
└──────────────┘  /api/v1   └──────┬───────┘        │ motor dosyaları (makale önbelleği, sözlükler)│
                                   │ enqueue        └──────────────────────────────────────────────┘
                                   ▼                         ▲
                            ┌──────────────┐  Redis kuyruğu  │ prepare / save
                            │ worker (×M)  │ ────────────────┘
                            │ RQ + motor   │  geçici çalışma dizini (silinebilir)
                            └──────────────┘
```

- **API** durumsuzdur, yatay ölçeklenir. İş durumu ve ilerleme veritabanında; arayüz sorgulayarak sayfa yenilemeden gösterir.
- **Worker** uzun analizleri çalıştırır. Dış veri kaynaklarının hız sınırı ortak olduğundan işler sırayla koşar; birden fazla
  worker açıldığında Redis kuyruğu dağıtır.
- **Kod deposunda ve sunucu diskinde kalıcı veri yoktur** (ayrıntı aşağıda, "Her şey veritabanında").

## Mimari

```
src/
├── kesif_engine/        Bilimsel çekirdek. Saf Python + pandas. Web/DB bilmez. Dosya tabanlı çalışır.
│   ├── pipeline.py          adımlar: fetch → drugs → labels → tune → rank → backtest → novelty → report
│   ├── ranking.py           Swanson ABC puanlama, köprü gen seçimi, özgüllük
│   ├── backtest.py …        doğrulama, yenilik, keşif özeti, dış servis istemcileri
│   └── resources/           pakete gömülü varsayılan hastalık kataloğu
└── kesif_server/        Uygulama. Bağımlılık yönü daima içeri: interfaces → application → domain ← infrastructure
    ├── domain/              entities.py (User, Disease, AnalysisRun, Candidate…), errors.py, ports.py (Protocol'ler)
    ├── application/         use_cases/ (diseases, analyses, candidates, catalog, auth), authorization.py
    ├── infrastructure/      db/ (SQLAlchemy 2 modelleri, depolar, UoW) · engine/ (motor adaptörü, ilerleme çevirisi, çalışma dizini ↔ DB)
    │                        queue/ (süreç içi · Redis/RQ) · auth/ (argon2, JWT, OIDC)
    ├── interfaces/          api/ (FastAPI v1: şemalar, router'lar, hata eşleme, middleware) · cli/ (typer)
    ├── container.py         bileşim kökü: ayarlardan somut nesneler
    └── core/                settings (pydantic-settings), logging (JSON), clock
alembic/                     şema sürümleri (migration)
tests/                       engine/ (bilimsel) · server/ (API, sahte motorla; ağ yok)
```

İlkeler:

- **Bağımlılık tersine çevirme:** kullanım senaryoları `domain.ports` sözleşmelerini alır; FastAPI, SQLAlchemy, Redis
  yalnızca `infrastructure` ve `interfaces` içinde görünür. Motor değişince API, API değişince motor etkilenmez.
- **Arayüz ayrımı:** motor dört küçük port'a bölünmüştür (çalıştırma, sonuç okuma, hastalık sözlüğü, dış servisler).
- **Tek sorumluluk:** her kullanım senaryosu tek iş yapar; yetki kuralı tek yerde (`application/authorization.py`).
- **Her şey veritabanında:** kullanıcılar, hastalıklar (+ parametreler), iş kuyruğu/ilerleme/günlük, aday listesi, diğer
  tüm çıktılar (`analysis_artifacts`) ve motorun çalışma dosyaları (`engine_files`, blob). Motor dosya tabanlı çalışmayı
  sürdürür ama geçici bir dizinde: `infrastructure/engine/workspace.py` işten önce dosyaları DB'den çeker, sonra
  değişenleri geri yazar; `SyncResults` çıktıları sorgulanabilir tablolara açar. Diskte kalıcı hiçbir şey yoktur.
- **Hatalar tek biçimli:** alan hataları HTTP koduna eşlenir; gövde `{"error": {"code", "message", "details", "request_id"}}`.

## Çalıştırma (geliştirme)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
kesif migrate
kesif api --reload
```

API: http://127.0.0.1:8000/api/v1/docs (OpenAPI). Arayüzü çalıştırmak için frontend reposunda `npm run dev`; ikisi aynı
anda, ayrı terminallerde çalışır. İlk giriş: `.env` içindeki `KESIF_BOOTSTRAP_ADMIN_EMAIL` / `..._PASSWORD`.

Eski dosya tabanlı kurulumdan geçiş (hastalık tanımları, parametreler, makale önbelleği ve sonuçlar veritabanına):

```bash
kesif import-legacy /eski/kurulum
```

Komut, verilen klasördeki `data/` ve `results/` içeriğini veritabanına yazar ve hastalık başına aktarılan aday/dosya
sayısını yazdırır. Bu sayıları doğrulamadan eski klasörleri silmeyin.

## Çalıştırma (canlı)

```bash
cp .env.compose.example .env
docker compose up -d --build
docker compose up -d --scale api=3 --scale worker=2
```

`docker-compose.yml`: `postgres`, `redis`, `migrate` (şemayı günceller ve çıkar), `api` (2 uvicorn işçisi), `worker` (RQ),
`frontend` (Nginx). Frontend imajı frontend reposundan derlenir: varsayılan yol `../frontend`, farklıysa `.env` içinde
`FRONTEND_CONTEXT` ile verilir (ya da `FRONTEND_IMAGE` ile hazır imaj kullanılır). Nginx statik dosyaları sunar ve `/api/`
isteklerini `api`ye yönlendirir; TLS'i önündeki ters vekil (ör. Sağlıktan Nginx'i) sonlandırır.

`KESIF_ENV=prod` iken sunucu güvensiz varsayılanlarla (SQLite, süreç içi kuyruk, varsayılan JWT gizi, kapalı kimlik)
açılmayı reddeder.

## Kimlik

- `local`: e-posta + parola (argon2id), erişim 60 dk + yenileme 14 gün JWT. İlk yönetici `KESIF_BOOTSTRAP_ADMIN_*`
  ile ya da `kesif create-admin`.
- `oidc`: Keycloak/Auth0/Sağlıktan SSO belirteçleri JWKS ile doğrulanır; kullanıcı ilk girişte açılır; rol
  belirteçteki `roles` / `realm_access.roles` içinden (`kesif-admin`, `kesif-analyst`), yoksa `viewer`.
- `disabled`: yalnızca geliştirme; her istek sistem yöneticisi olarak çalışır.

Roller: `viewer` okur · `analyst` hastalık ekler, analiz başlatır, parametre değiştirir, hastalığı listeden kaldırır ·
`admin` kullanıcı yönetir, veriyi kalıcı siler.

## Komutlar

| Komut | İş |
|---|---|
| `kesif migrate` | Alembic `upgrade head` |
| `kesif api` | REST API |
| `kesif worker` | Redis kuyruğu işçisi (birden fazla açılabilir) |
| `kesif run <hastalık> -s fetch -s rank` | analizi bu süreçte çalıştır |
| `kesif create-admin <e-posta>` | yönetici aç |
| `kesif import-legacy <klasör>` | eski kurulumu al |
| `kesif doctor` | dış veri kaynaklarını denetle |

## Test ve kalite

```bash
pytest
ruff check src tests
mypy
```

`pytest` motor ve API testlerini çalıştırır; API testleri sahte motorla ve bellek içi SQLite ile koşar, ağ gerektirmez.
CI (`.github/workflows/ci.yml`) her push'ta Python 3.9 ve 3.12'de lint, test, migration ve Docker imaj derlemesini yapar.

Yeni migration: `alembic revision --autogenerate -m "..."` (env.py bağlantıyı `KESIF_DATABASE_URL`'den alır).
