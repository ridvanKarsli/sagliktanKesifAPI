# Sağlıktan Keşif — Backend

Bilimsel makaleleri ve klinik çalışma kayıtlarını analiz ederek, bugün kullanılan ilaçlar arasından belirli bir
hastalıkta **henüz denenmemiş ama umut vadeden** adayları bulan ve öncelik sırasına koyan platformun sunucu tarafı.

Bu repo iki parçadan oluşur:

- **`kesif_engine`** — bilimsel motor: veri toplama, aday üretme, puanlama, geriye dönük doğrulama.
- **`kesif_server`** — REST API, kullanıcı/yetki yönetimi, iş kuyruğu ve veritabanı (Clean Architecture).

Arayüz ayrı bir repodadır (Sağlıktan Keşif — Frontend) ve bu API'ye yalnızca `/api/v1` üzerinden bağlanır.

---

## Kısaca nasıl çalışır

1. **Veri toplama.** Hastalığın makaleleri (PubMed, PubTator anotasyonları), ilaç sözlüğü (ChEMBL), genetik kanıt
   (Open Targets), protein ağı (STRING) ve tüm girişimsel klinik çalışmalar (ClinicalTrials.gov) toplanır.
2. **Aday üretme.** İlaçlar hastalığa farklı **kanıt kanallarıyla** bağlanır (tablo aşağıda).
3. **Sıralama.** Kanallar, geçmiş verilerle eğitilen şeffaf bir lojistik modelle birleştirilir. Hiçbir aşamada dil
   modeli kullanılmaz; aynı veri her zaman aynı sıralamayı verir.
4. **Açıklama.** Her aday için "neden bu ilaç?" adımları (kaynak makale PMID'leri ve klinik çalışma NCT'leriyle), 5 yıl
   içinde klinik çalışmaya girme olasılığı ve gerekirse uyarılar (ör. benzer hastalıkta etkisizlik nedeniyle durdurulmuş
   çalışma) üretilir.
5. **Doğrulama.** Yöntem, geçmişe dönük ve zaman dilimli testlerle ölçülür: yalnız kesit yılına (2012/2015/2018) kadarki
   veri kullanılır, sonraki yıllarda gerçekten klinik çalışmaya giren ilaçlar ne kadar üst sırada bulunmuş, ona bakılır.

### Kanıt kanalları

| Kanal | Ne ölçer | Kaynak |
|---|---|---|
| `similar` | ilacın, bu hastalığa en çok benzeyen hastalıklarda daha önce klinik çalışmaya girmiş olması | ClinicalTrials.gov atlası |
| `lit` | hastalık → köprü gen → ilaç dolaylı bağı (Swanson ABC, lift ve bağlam ağırlıklı) | makaleler |
| `mech` | ilacın bilinen hedefi köprü genlerden biri mi | ChEMBL |
| `direct`, `recent` | ilacın hastalık literatüründe doğrudan ve son 5 yılda anılması | makaleler |
| `accel` | kanıt ivmesi: ilginin son yıllarda hızlanması | makaleler |
| `genetic`, `biology` | hedef genin hastalıkla genetik ilişkisi; hayvan modeli, gen ifadesi, yolak kanıtı | Open Targets |
| `network` | hedefin protein ağında hastalık genlerine yakınlığı (derece eşlemeli z-skoru) | STRING |
| `frequency` | popülerlik (aynı zamanda taban çizgisi) | makaleler |

Benzerlik kanalı ve atlas tamamen zaman dilimlidir: benzerlik de yalnız kesitten önceki klinik çalışmalardan hesaplanır.

Kanalların birleştirme ağırlıkları ve olasılık kalibrasyonu ölçülmüş hâliyle pakettedir
(`resources/fusion_default.json`); yeni bir kurulum hiç ölçüm yapmadan aynı modeli kullanır. `kesif benchmark`
çalıştırılırsa kurulum kendi ölçümünü kullanmaya başlar.

### Güncel ölçüm sonuçları

| Test | Sonuç |
|---|---|
| Seçim seti (52 hastalık × 3 kesit), popülerliğe göre AP farkı | **+0,055** [0,044; 0,067] (%90 GA) |
| Yöntem tasarlanırken hiç bakılmamış 10 hastalık | **+0,088** [0,041; 0,134] |
| 5 yıllık olasılık kalibrasyonu (Brier becerisi) | geçti; ilk 5 sıra: tahmin %9,2, gerçekleşen %8,7 |
| TxGNN (Harvard, 2024) ile, ikisi de yalnız 2021 bilgisiyle, 2022+ ilk klinik çalışmalar | AP 0,125'e 0,017; 62 hastalığın 59'unda önde |
| Aynı karşılaştırma, 2021 sonrası onaylanan yeni kullanımlar | AP 0,076'ya 0,030; 11 hastalığın 10'unda önde |

Doğrulama etiketleri (hangi çalışma "bu ilacı bu hastalıkta deniyor" sayılır) kurallarla çıkarılır ve yaklaşık 2.900
satır elle denetlenmiştir; yanlışlar ölçümden çıkarılır.

---

## Hızlı başlangıç (geliştirme)

Gerekenler: Python 3.9+, Docker (yerel PostgreSQL için).

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                     # gerekli değerleri doldurun (parolalar repoya girmez)

# yerel PostgreSQL (port 5433)
POSTGRES_PASSWORD=<parola> docker compose -f docker-compose.dev.yml up -d
# .env: KESIF_DATABASE_URL=postgresql+psycopg://kesif:<parola>@localhost:5433/kesif

kesif migrate                            # şemayı oluştur
kesif api --reload                       # http://127.0.0.1:8000/api/v1/docs
```

Arayüz için frontend reposunda `npm run dev` (ayrı terminalde). İlk giriş: `.env` içindeki
`KESIF_BOOTSTRAP_ADMIN_EMAIL` / `KESIF_BOOTSTRAP_ADMIN_PASSWORD`.

İlk kurulumda bir kez (yaklaşık 5 dakika; "benzer hastalıklarda denenmiş" kanalı için gerekli):

```bash
kesif atlas                              # ClinicalTrials.gov'u indirir, klinik çalışma atlasını derler
```

Hastalıklar arayüzden eklenip analiz edilebilir. Komut satırından uçtan uca analiz (kuyruk ve sunucu gerekmez):

```bash
kesif run behcet                         # veri toplama → etiketler → kanıt → sıralama → doğrulama → rapor
kesif run behcet -s rank -s report       # yalnız belirli adımlar
```

---

## Proje yapısı

```
src/
├── kesif_engine/            Bilimsel çekirdek. Saf Python + pandas; web ve veritabanı bilmez.
│   ├── sources/                 dış kaynaklar: pubmed · pubtator · chembl · opentargets · string_ppi ·
│   │                            clinicaltrials · trial_atlas (tüm çalışmalardan ilaç × hastalık × ilk yıl)
│   ├── extraction/              kimyasal → ilaç eşlemesi, metinden biyolojik ilaç taraması
│   ├── catalog/                 hastalık tanımları, MeSH sözlüğü
│   ├── scoring/                 ranking (literatür köprüsü) · features (kanallar) · fusion (birleştirme) · network
│   ├── evaluation/              labels (doğrulama etiketleri) · backtest · benchmark · calibration · atlas
│   ├── insights/                explain (kanıt zinciri) · discovery (keşif özeti) · novelty · confidence · report
│   ├── pipeline/                adımların orkestrasyonu, tahmin defteri (ledger)
│   ├── core/                    ayarlar, HTTP, önbellek, dizin düzeni
│   └── resources/               hastalık kataloğu, ölçüm paneli, hastalık terim sözlüğü
└── kesif_server/            Uygulama katmanı. Bağımlılık yönü: interfaces → application → domain ← infrastructure
    ├── domain/                  varlıklar, hatalar, port (Protocol) tanımları
    ├── application/             kullanım senaryoları, yetki kuralları
    ├── infrastructure/          SQLAlchemy depoları, motor adaptörü, kuyruk (süreç içi / Redis-RQ), kimlik (argon2, JWT, OIDC)
    ├── interfaces/              api/ (FastAPI v1) · cli/ (typer: `kesif` komutu)
    └── container.py             bileşim kökü
alembic/                     veritabanı şema sürümleri
tests/                       engine/ (bilimsel) · server/ (API; sahte motorla, ağ gerektirmez)
```

Motor katmanları tek yönlüdür: `core → sources → catalog / extraction → scoring → evaluation → insights → pipeline`.
`tests/engine/test_architecture.py` bu kuralı her test çalıştırmasında denetler. Sıralama kalitesini etkileyen her
değişiklik `scoring/` ve `evaluation/` içinde yapılır.

**Kalıcı veri yalnızca veritabanındadır** (kullanıcılar, hastalıklar, işler, adaylar, çıktılar ve motorun çalışma
dosyaları). Motor işten önce dosyalarını veritabanından geçici bir dizine çeker, iş bitince değişenleri geri yazar.

---

## Komutlar

| Komut | Ne yapar |
|---|---|
| `kesif migrate` | veritabanı şemasını günceller |
| `kesif api` / `kesif worker` | REST API / Redis kuyruğu işçisi |
| `kesif create-admin <e-posta>` | yönetici hesabı açar |
| `kesif run <hastalık> [-s adım]` | analizi bu süreçte çalıştırır |
| `kesif doctor` | dış veri kaynaklarının erişilebilirliğini denetler |
| `kesif panel` / `kesif run-panel` | ölçüm paneli (68 hastalık) / paneli sırayla analiz eder |
| `kesif benchmark [--rerank]` | çok hastalıklı yöntem karnesi; birleştirme kararını ve kalibrasyonu ölçümle günceller |
| `kesif atlas [--restart]` | ClinicalTrials.gov'u indirir ve klinik çalışma atlasını derler |
| `kesif label-audit <hastalık>` | etiket denetim örneği çıkarır; `--import <csv>` ile işaretlenmişi içe alır |
| `kesif ledger freeze/verify/track/panel` | tahmin defteri: adayları zaman damgasıyla dondurur, sonradan doğrulanmayı ölçer |
| `kesif db-copy <kaynak> <hedef>` | veritabanını eksiksiz kopyalar ve doğrular |

---

## Ölçüm kuralları (değişiklik yaparken)

Projenin değeri, sonuçların dürüst ölçülmesine dayanır. Bu yüzden:

1. **Sızıntı yok.** Geriye dönük testte kesit yılından sonraki hiçbir bilgi (makale, çalışma, benzerlik) kullanılamaz.
2. **Her yöntem değişikliği ölçülür.** `kesif benchmark` ile seçim setinde, popülerliğe göre farkın %90 güven aralığı
   alt sınırı > 0 olmadan yeni bir kanal ya da ayar varsayılan yapılmaz.
3. **Kesin doğrulama setine bakılmaz.** Panelde `final: true` işaretli hastalıklar yöntem tasarlanırken kullanılmaz;
   yalnız son kararı doğrulamak için bir kez açılır, sonra `used_as_final` olarak işaretlenir.
4. **Tahmin defteri içeriği paylaşılmaz.** Dondurulan listeler (`ledger`) yalnızca SHA-256 özetiyle paylaşılır.

---

## Canlı ortam

```bash
cp .env.compose.example .env
docker compose up -d --build
docker compose up -d --scale api=3 --scale worker=2
```

Servisler: `postgres`, `redis`, `migrate`, `api`, `worker`, `frontend` (Nginx). Frontend imajı varsayılan olarak
`../frontend` yolundan derlenir (`FRONTEND_CONTEXT` ile değiştirilebilir). `KESIF_ENV=prod` iken sunucu güvensiz
varsayılanlarla (SQLite, varsayılan JWT gizi, kapalı kimlik) açılmayı reddeder.

## Kimlik ve roller

- **Kimlik modu:** `local` (e-posta + parola, JWT), `oidc` (kurumsal SSO) ya da `disabled` (yalnız geliştirme).
- **Roller:** `viewer` okur · `analyst` hastalık ekler, analiz başlatır, ayar değiştirir · `admin` kullanıcı yönetir,
  veriyi kalıcı siler.

## Test ve kalite

```bash
pytest                      # motor + API testleri (ağ gerektirmez)
ruff check src tests
mypy
```

CI her push'ta Python 3.9 ve 3.12'de lint, test, migration ve Docker derlemesini çalıştırır.
Yeni migration: `alembic revision --autogenerate -m "açıklama"`.

## Katkı

- `main` dalına doğrudan gönderim yapılmaz; her iş ayrı bir dalda (`ozellik/...`, `duzeltme/...`) ve pull request ile.
- PR'dan önce `pytest` ve `ruff` temiz olmalı.
- `.env`, parola, veritabanı dosyası ve büyük veri dosyaları repoya girmez.
