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
├── kesif_engine/        Bilimsel çekirdek (algoritma ve doğruluk). Saf Python + pandas. Web/DB bilmez.
│   ├── pipeline/            orkestrasyon: steps (fetch → drugs → labels → evidence → tune → rank → backtest → novelty →
│   │                        report), evidence (bağımsız kanıt), benchmark (yöntem karnesi), corpus, status, settings,
│   │                        results — dışarıya açık kapı
│   ├── insights/            novelty (yenilik kontrolü) · discovery (keşif özeti) · confidence (yöntem güveni) · report
│   ├── health.py            dış veri kaynaklarının sağlık kontrolü
│   ├── evaluation/          labels (doğrulama etiketleri) · backtest (geriye dönük test) · tuning (parametre ayarı)
│   │                        benchmark (çok hastalık × çok kesit ölçümü, güven aralığı, birleştirme kararı)
│   ├── scoring/             ranking (literatür köprüsü, özgüllük, yön) · features (kanıt kanalları) · fusion (kanal
│   │                        birleştirme) · network (protein ağında yakınlık) · drugspace (ilaç sınıfları)
│   ├── catalog/             diseases (hastalık tanımları) · mesh (hastalık sözlüğü)
│   ├── extraction/          drugmap (kimyasal → ilaç) · textmatch (metinden biyolojik ilaç taraması)
│   ├── sources/             pubmed · pubtator · chembl · genes · clinicaltrials · opentargets · string_ppi
│   ├── core/                config (ayarlar, kelime listeleri) · net (HTTP) · store (önbellek) · paths (dizin düzeni)
│   └── resources/           varsayılan hastalık kataloğu · ölçüm paneli · paketle gelen birleştirme kararı
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

Motorun katmanları da tek yönlüdür: `core → sources → catalog / extraction → scoring → evaluation → insights → pipeline`.
Bir modül yalnızca kendi katmanından ya da altından içe aktarabilir ve motor sunucuyu hiç içe aktarmaz;
`tests/engine/test_architecture.py` bunu her test çalıştırmasında denetler. Doğruluk ve keşif kalitesiyle ilgili her
değişiklik `scoring/` ve `evaluation/` içinde yapılır.

## Algoritma

Motor tek bir puan formülü değil, ayrı ayrı ölçülebilen **kanıt kanallarıdır**; kanalların nasıl birleştirileceğine
geriye dönük ölçüm karar verir. Hiçbir aşamada dil modeli ya da kararı dışarıya bırakan bir bileşen yoktur: aynı veri
her zaman aynı sıralamayı verir (`tests/engine/test_determinism.py`).

| Kanal | Ne ölçer | Kaynak |
|---|---|---|
| `lit` | hastalık → köprü gen → ilaç dolaylı bağı (Swanson ABC, lift ve bağlam ağırlıklı) | makaleler |
| `mech` | ilacın bilinen hedefi köprü genlerden biri mi | ilaç veritabanı |
| `direct`, `recent` | ilacın hastalık literatüründe doğrudan ve son 5 yılda anılması | makaleler |
| `genetic` | ilaç hedefinin hastalıkla genetik ilişkisi (bilinen ilaç ve literatür kanıtı **dışlanır**) | Open Targets |
| `biology` | hayvan modeli, gen ifadesi, yolak kanıtı | Open Targets |
| `network` | hedefin protein ağında hastalık genlerine yakınlığı: RWR + iki yönlü derece eşlemeli z-skoru (metin madenciliği kenarları **dışlanır**) | STRING |

Birleştirme seçenekleri ve seçim kuralı (`evaluation/benchmark.py`):

- **Temel yöntem:** literatür köprüsü + mekanizma, zarar cezasıyla.
- **Kanal birleştirme (RRF, Cormack 2009):** her kanaldaki sıranın `1/(60+sıra)` toplamı; ölçekten bağımsız, eğitim gerektirmez.
- **Öğrenen birleştirme:** kanıt kanallarının katsayısı negatif olamayan, açıklanabilir lojistik model; yalnızca
  eğitimde görmediği hastalıkta ölçülür (hastalık dışarıda bırakılarak).
- Seçim: her hastalık 2012, 2015 ve 2018 kesitlerinde test edilir; bir yöntem temel yöntemi eşleştirilmiş farkın %90
  güven aralığıyla geçmedikçe benimsenmez. Paneldeki kontrol hastalıkları karara katılmaz, sonradan raporlanır.

Geliştirme ölçümü (Behçet, ailesel Akdeniz ateşi, retinitis pigmentosa; 9 test; klinik çalışma etiketi; ortalama AP),
etiket temizliğinden sonra: temel yöntem 0,181, kanal birleştirme 0,186 (fark %90 GA [−0,017; 0,025]), öğrenen model
0,166, bağlantı terimi sayısı (LTC) 0,161, sıklık 0,087, rastgele 0,009. Temizlikten önce kanal birleştirme anlamlı
biçimde öndeydi; kazancın bir kısmı aynı ilacın tuz biçimlerinin ayrı sayılmasından geliyordu. Fark artık anlamlı
olmadığı için paketle gelen karar temel yöntemdir; kurulum `kesif benchmark` çalıştırdığında kendi ölçümü bunun yerini
alır. Genetik ve ağ kanalları birleştirmeye ancak ölçümde katkıları gösterilirse girer.

Değerlendirilip alınmayanlar: graf sinir ağları (TxGNN gibi; GPU, açıklanamayan karar ve tekrarlanabilirlik sorunu),
gen ifadesi imza eşleştirme (CMap/LINCS; lisans ve hastalık başına imza gerekliliği).

**Doğrulama etiketleri** (`evaluation/labels.py`, şema 3): yalnızca girişimsel, tedavi/önleme amaçlı çalışmaların
deneysel kolundaki ilaçlar sayılır. Karşılaştırma ya da plasebo kolunda da bulunan ilaçlar (arka plan tedavisi), tanı
ajanları, görüntüleme izleyicileri ve yardımcı maddeler, 5'ten fazla hastalık listeleyen şemsiye çalışmalar sayılmaz;
tuz biçimleri ana moleküle birleşir. İkinci etiket `trials_late` yalnızca faz 2+ çalışmalardır; bir yöntem birincil
etikette kazanıp bu etikette geriliyorsa benimsenmez. `kesif label-audit` her hastalıktan örnek çıkarır, yanlış
bulunanlar ölçümden çıkar ve hata oranı Kapı 1'e girer. Her geriye dönük test, kesite göre dilimlenemeyen girdileri
(ilaç sözlüğü, gen arka plan sayıları, genetik, ağ) `leakage` alanında ayrıca raporlar.

**Kapı 1** (`kesif benchmark` çıktısı): ≥ 25 hastalık ölçülmüş, etiket hatası < %5 ve kullanılan yöntem sıklık, LTC ve
(varsa) tek başına ağ yakınlığı taban çizgilerini %90 güven aralığıyla geçiyor.

**Yöntem güveni** (`insights/confidence.py`): her hastalık için ölçüme dayalı not — yüksek / orta / düşük / ölçülemedi.
Rastgeleye oran, sıklık sıralamasına göre durum, ilk 25'teki isabet ve örnek sayısından kural tabanlı türetilir;
arayüzde ve raporda gerekçeleriyle gösterilir (ör. retinitis pigmentosa'da "düşük": listenin başında doğrulanan ilaç yok).

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
| `kesif benchmark` | derlemi hazır hastalıklarda yöntem karnesi; birleştirme kararını ölçümle günceller |
| `kesif panel --add` | 32 hastalıklık ölçüm panelini kataloğa ekle (6'sı karara katılmayan kontrol) |
| `kesif run-panel` | paneldeki hastalıkları sırayla analiz et; tamamlananları atlar, hata vereni geçer |
| `kesif label-audit <hastalık>` | etiketlerden 50 satırlık denetim örneği çıkar; `--import <csv>` ile işaretlenmişi geri al |

## Test ve kalite

```bash
pytest
ruff check src tests
mypy
```

`pytest` motor ve API testlerini çalıştırır; API testleri sahte motorla ve bellek içi SQLite ile koşar, ağ gerektirmez.
CI (`.github/workflows/ci.yml`) her push'ta Python 3.9 ve 3.12'de lint, test, migration ve Docker imaj derlemesini yapar.

Yeni migration: `alembic revision --autogenerate -m "..."` (env.py bağlantıyı `KESIF_DATABASE_URL`'den alır).
