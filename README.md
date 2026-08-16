# Total Rasyo Hesaplayıcı — Period-Based RSC + Expected Band + M2

Bu proje, 8 finansal dönemlik oran kalitesini fiyatla karşılaştıran bir karar destek motorudur.

Ana soru:

> Son 8 dönemde total rasyo notu yükseliyor mu, beklenen fiyat bandları dönem dönem yukarı gidiyor mu, ama gerçek fiyat bu band yükselişini takip etmeyip geride mi kalıyor?

## Yeni ana mantık

Bu sürümde M1 ve M2 yeniden düzenlendi.

### M1 — 8 dönem finansal kalite trendi

M1 artık sadece son RSC skoru değildir. Şunlara bakar:

- son 8 finansal dönem total rasyo notu,
- son dönem notu,
- önceki dönem notu,
- 1 dönemlik değişim,
- 4 dönemlik değişim,
- 8 dönemlik eğim,
- güçlü oran sayısı değişimi.

Çıktı:

```text
analytics.period_8q_comparison
```

### M2 — dönemsel beklenen band / fiyatlama sapması

M2 artık tek günlük “fiyat bandın altında mı?” sorusu değildir. Şunları sorar:

- Bu dönem beklenen band ne?
- Önceki dönem beklenen band neydi?
- Bugünkü fiyat eski banda göre nerede?
- Bugünkü fiyat yeni banda göre nerede?
- Band ortası yükseldi mi?
- Fiyat band yükselişini takip ediyor mu?
- Son 8 dönemde band yükselirken fiyat geride mi kalmış?
- Son 63 günlük trailing alpha bunu destekliyor mu?

Çıktı:

```text
analytics.expected_band_periods
analytics.m2_period_comparison
```

Örnek M2 yorumu:

```text
Bu dönem beklenen band 28-40.
Önceki dönem band 24-34.
Band ortası 29'dan 34'e çıktı.
Bugünkü fiyat 25.
Fiyat önceki banda göre bandın alt/orta kısmında, yeni banda göre bandın altında.
Son 8 dönemde total rasyo notu yükseliyor.
Beklenen bandlar dönem dönem yukarı gidiyor.
Fiyat bu band yükselişini takip etmiyor, geride kalıyor.
Son 63 günlük alpha hâlâ zayıf ama toparlanma varsa fırsat güçlenir.
```

### M3 — son 63 işlem günü trailing alpha

Ana sistem artık rapor sonrası 63 işlem günü beklemez. Analiz gününden geriye doğru son 63 işlem gününü ölçer:

```text
asof_date - 63 işlem günü → asof_date
```

Hisse getirisi, BIST100 getirisi ve sektör endeksi getirisi ayrıştırılır.

Çıktı:

```text
analytics.alpha_trailing
```

Eski `alpha_realized` ve `decile_map` yapısı geriye dönük kalibrasyon/backtest için korunmuştur.

## Kurulum

```bash
pip install -r requirements.txt
```

Veritabanı tabloları:

```bash
psql -f sql/010_create_core_tables.sql
psql -f sql/000_create_schemas.sql
psql -f sql/001_create_analytics_tables.sql
psql -f sql/003_decile_thresholds.sql
psql -f sql/005_backtest_tables.sql
psql -f sql/006_trailing_alpha_period_m2_tables.sql
psql -f sql/007_valuation_and_backtest_ext.sql
```

Ya da Makefile ile:

```bash
make core
make migrate
```

## Veri akışı

1. Hisse evrenini yükle:

```bash
python -m src.app.cli ingest-universe --file data/universe_stocks.csv
```

2. Hisse fiyatlarını yükle:

```bash
python -m src.app.cli ingest-prices --file data/prices_daily.csv
```

3. Endeks fiyatlarını yükle:

```bash
python -m src.app.cli ingest-index --file data/index_prices_daily.csv
```

4. Finansal verileri yükle:

```bash
python -m src.app.cli ingest-fin --file data/financials_quarterly.csv
```

5. Oranları hesapla:

```bash
python -m src.app.cli calc-ratios --ratios config/ratios.json
```

6. Ana pipeline'ı çalıştır:

```bash
python -m src.app.cli run-daily --asof 2026-02-20 --ratios config/ratios.json --sectors config/sectors.json --weights config/weights.json
```

## Pipeline sırası

```text
core.financials_quarterly
→ analytics.ratios_quarterly
→ analytics.rsc_summary_quarterly
→ analytics.beta_estimates
→ analytics.alpha_trailing
→ analytics.period_8q_comparison
→ analytics.expected_band_periods
→ analytics.m2_period_comparison
→ analytics.module_scores
```

## Backtest

```bash
python -m src.app.cli backtest \
  --start 2024-01-01 --end 2026-01-01 \
  --rebalance step5 --hold 20 \
  --ensure-scores \
  --ratios config/ratios.json --sectors config/sectors.json --weights config/weights.json
```

Çıktılar:

```text
analytics.backtest_runs
analytics.backtest_timeseries
outputs/backtest_<run_id>.csv
```

## Notlar

- Haber/KAP sentiment bu aşamada bilinçli olarak dışarıda bırakılmıştır.
- Sistem doğrudan yatırım yapmaz; karar destek motorudur.
- Erken küçük veri setlerinde `decile_map` boş kalabilir. Bu durumda `expected_band_periods` muhafazakâr RSC fallback modeliyle çalışır.
- Daha güçlü sonuç için geniş hisse evreni gerekir.

## BANK v4.7 point-in-time entegrasyonu

Banka değerleme motoru point-in-time veri, toplu çalışma, M2 ve Total Rasyo
akışına bağlandı. Üretim zinciri:

```text
TIMESTAMPTZ açıklama anı
→ sekiz sabit takvim çeyreği
→ deterministik ORIGINAL/RESTATED seçimi
→ Python float/None kanonik dönüşümü
→ point-in-time COE / macro_cap / risksiz faiz varsayımı
→ leave-one-out sektör artık tabanı
→ estimate_roe_uncertainty
→ bank_valuation(sd_roe=...)
→ dört çarpanlı v_conf
→ BANK iki eksenli M2
→ module_scores / Total Rasyo köprüsü
```

Migration:

```bash
psql -f sql/000_create_schemas.sql
psql -f sql/011_bank_valuation_integration.sql
psql -f sql/013_bank_batch_m2_integration.sql
```

Point-in-time banka metriklerini ve varsayımlarını yükle:

```bash
python -m src.app.cli ingest-bank-metrics \
  --file data_templates/bank_metrics_quarterly.csv

python -m src.app.cli ingest-bank-assumptions \
  --file data_templates/bank_valuation_assumptions.csv
```

Bütün aktif bankaları tek batch çağrısıyla değerlendir:

```bash
python -m src.app.cli run-bank-batch \
  --analysis-at 2026-08-04T19:00:00+03:00 \
  --anchor 2026-06-30
```

BANK M2 sonucunu günlük Total Rasyo çalışmasına bağla:

```bash
python -m src.app.cli run-daily \
  --asof 2026-08-04 \
  --analysis-at 2026-08-04T19:00:00+03:00 \
  --anchor 2026-06-30 \
  --ratios config/ratios.json \
  --sectors config/sectors.json \
  --weights config/weights.json
```

`--analysis-at` verildiğinde bütün DATE tabanlı piyasa modülleri aynı muhafazakâr
kapanış kesimini kullanır. İstanbul saatiyle 18:30 öncesinde aynı gün kapanışı
kullanılmaz. Bu, BANK M2 kadar M3, momentum, volatilite ve eski dönemsel M2
yolunun da gün içi gelecek verisi görmesini engeller. Gerçek zaman damgalı fiyat
akışı geldiğinde bu geçici daily-close adaptörü ayrıca değiştirilecektir.

Gölge kalibrasyon raporu:

```bash
python -m src.app.cli bank-shadow-report \
  --analysis-at 2026-08-04T19:00:00+03:00 \
  --thresholds 0.80,0.90,1.00 \
  --report-out outputs/bank_shadow_2026-08-04.csv
```

Rapor sert band kapısını değiştirmez. Sektör × dönem bazında ret oranı,
`justified_pb`, `z_val`, `s_valuation` doyumu, gerçek floor-binding oranı,
uç değer oranı, COE, makro tavan, risksiz faiz ve fiyat/`V_mid` dağılımlarını
üretir.

Hızlı testler ve iki katmanlı öz denetim:

```bash
PYTHONPATH=. pytest -q tests
PYTHONPATH=. python scripts/self_audit_bank_integration.py
PYTHONPATH=. python scripts/self_audit_bank_batch.py
PYTHONPATH="$PWD/src/analytics/bank_v47" \
  pytest -q vendor/v47_roe_belirsizlik
```

Canlı PostgreSQL kabul koşusu:

```bash
PYTHONPATH=. python scripts/run_postgres_bank_acceptance.py
```

Bu kabul çalıştırıcısı sessizce `skip` etmez. `psql`, migration, SQL veya bağlantı
sorunu varsa sert biçimde başarısız olur.

### Uygulanan fail-closed kuralları

- `published_at`, `analysis_at` ve varsayım `effective_at` alanları `TIMESTAMPTZ`.
- Sekiz yuva ve kronolojik sıra zorunlu.
- Eşit yayın anında `published_at DESC, version_sequence DESC, record_id DESC`.
- Varsayım kaydı analiz anından ileri olamaz.
- Seçilmiş finansal yayın analiz anından ileri olamaz.
- Günlük fiyat, İstanbul kapanış kesiminden ileri olamaz.
- M2 skor girdileri ve tanılar ayrı JSON nesnelerinde tutulur.
- BANK motoru yalnız `sector_code=BANK` veya `XBANK` evrenine uygulanır;
  geniş `XUMAL` endeksi BANK motoruna otomatik yönlendirilmez.

### Bu pakette henüz tamamlanmayanlar

- Gerçek KAP/MKK veri sağlayıcısına bağlanma
- COE, risksiz faiz ve `macro_cap` varsayımlarının otomatik point-in-time kaynağı
- Zaman damgalı gerçek zaman/gecikmeli piyasa fiyatı entegrasyonu
- Tüm bankalarla canlı PostgreSQL performans ve dağılım koşusu
- Canlı PostgreSQL 16 kabul koşusunun bu çalışma ortamında tekrarlanması
- Holding, GYO, sigorta, diğer finans ve finans dışı değerleme motorları
- Eski sektör taksonomisindeki geniş `XUMAL → BANK` RSC eşlemesinin yeni
  sektör motorları geliştirilirken ayrıştırılması


## Resmî KAP/MKK veri omurgası

Bu sürüm, bütün sektör motorlarının kullanacağı kayıpsız resmî KAP veri katmanını
ekler. İki kaynak bilinçli olarak ayrılmıştır:

1. KAP'ın resmî `BIST Şirketleri` sayfası yalnız aktif şirket/ticker evrenini
   başlatmak ve güncellemek için kullanılır.
2. Finansal bildirim ve finansal kalemler MKK API Portal'da kayıt olunan resmî
   KAP veri ürünü üzerinden alınır.

KAP özet finansal sayfaları point-in-time finansal veri kaynağı olarak
kullanılmaz. API ürününün URL, yol, kimlik doğrulama header'ı ve JSON alanları
belgelenmeden tahmin edilmez. Örnek config dosyaları bu nedenle çalışmayan
`.example.invalid` değerleri taşır.

Migration:

```bash
psql -f sql/015_kap_official_ingestion.sql
psql -f sql/019_mkk_kap_runtime_safety.sql
```

Aktif BIST evrenini yalnız dosyaya al:

```bash
python -m src.app.cli fetch-kap-universe \
  --out data/universe_kap.csv \
  --minimum-rows 100
```

Evreni PostgreSQL'e de eşitle:

```bash
python -m src.app.cli sync-kap-universe \
  --out data/universe_kap.csv \
  --minimum-rows 100
```

MKK API Portal'da uygulama/API anahtarı ve KAP ürünü kaydı tamamlandıktan sonra:

```bash
cp config/mkk_kap_endpoints.example.json config/mkk_kap_endpoints.json
# Endpoint ve alan yollarını yalnız resmî ürün dokümanına göre doldur.
export MKK_API_KEY='<portal-api-key>'

# Önce endpoint/auth/JSON sözleşmesini PostgreSQL'e dokunmadan doğrula.
python -m src.app.cli check-mkk-kap \
  --api-config config/mkk_kap_endpoints.json \
  --start 2026-08-04T00:00:00+03:00 \
  --end 2026-08-04T00:05:00+03:00

python -m src.app.cli sync-mkk-kap \
  --api-config config/mkk_kap_endpoints.json \
  --start 2026-08-04T00:00:00+03:00 \
  --end 2026-08-04T23:59:59+03:00 \
  --stream-name financial-statements

# Son başarılı checkpoint'ten kontrollü overlap ile devam et.
python -m src.app.cli sync-mkk-kap \
  --resume \
  --api-config config/mkk_kap_endpoints.json \
  --end 2026-08-05T00:00:00+03:00 \
  --stream-name financial-statements \
  --overlap-seconds 300 \
  --max-window-hours 24
```

Ham bildirimleri genel finansal fact tablosuna ayır:

```bash
cp config/mkk_kap_financial_facts_mapping.example.json \
   config/mkk_kap_financial_facts_mapping.json
# JSON yollarını yalnız kayıt olunan ürün dokümanına göre doldur.

python -m src.app.cli extract-kap-facts \
  --mapping-config config/mkk_kap_financial_facts_mapping.json \
  --analysis-at 2026-08-04T19:00:00+03:00 \
  --notification-type FINANCIAL_STATEMENT \
  --limit 1000
```

Katmanlar:

```text
KAP BIST şirket evreni
→ core.universe_stocks

MKK KAP API ham bildirim
→ raw.kap_disclosures (değişmez payload + SHA256)
→ raw.kap_sync_state (geriye gitmeyen checkpoint)
→ raw.kap_sync_runs (her denemenin COMPLETE/QUARANTINED izi)
→ raw.kap_api_quarantine (bozuk item payload + SHA + neden)
→ raw.kap_financial_facts (mapping sürümlü kayıpsız fact)
→ raw.kap_fact_extraction_rejections (yeniden denenebilir ret defteri)
```

Fail-closed davranışlar:

- Bütün yayın ve alım anları timezone içeren `TIMESTAMPTZ` olmak zorunda.
- Aynı bildirim kimliğinin payload veya yayın anı sonradan değiştirilemez.
- Cursor tekrarı, sayfa sınırı, gelecek yayın, bozuk JSON ve farklı payload'lı
  duplicate kimlik sert ret alır.
- Fact tarihleri kırpılmaz; bozuk tarih, bool/sonsuz/aşırı büyük sayı, boş fact
  listesi ve yapılandırılmış metin alanları ret defterine gider.
- Fact context ve değerleri değişmezdir; yalnız `extracted_at` ileri taşınabilir.
- CLI kuru API alımı PostgreSQL sürücüsünü gerektirmez.
- Örnek/placeholder endpoint canlı çağrıdan önce reddedilir.
- `Retry-After`, asgari istek aralığı, response/item byte sınırları uygulanır.
- Bozuk item karantinaya alınırsa geçerli itemler saklanabilir fakat checkpoint
  ilerlemez ve komut başarılı sayılmaz.

Doğrulama:

```bash
PYTHONPATH=. pytest -q tests
PYTHONPATH=. python scripts/self_audit_kap_ingestion.py
python scripts/self_audit_mkk_runtime.py
```

Canlı çalışma sözleşmesinin ayrıntıları:

```text
docs/MKK_CANLI_RUNTIME_V9.md
```

Bu paket resmî API ürün sözleşmesine hazır bir adaptördür; gerçek MKK endpoint'i,
API anahtarı ve ürün JSON mapping'i bu çalışma ortamında bulunmadığından canlı
MKK çağrısı yapıldığı iddia edilmez.

## Sürümlü semantik finansal kalem ve BANK türetim katmanı

Ham KAP kalemleri sektör motorlarına doğrudan yazılmaz. Arada kaynak kodundan
bağımsız, sürümlü ve lineage taşıyan ortak semantik katman bulunur:

```text
raw.kap_financial_facts
→ SemanticFactMapper(mapping_profile + mapping_version)
→ core.semantic_financial_facts
→ BANK türetim profili
→ core.bank_metrics_quarterly
→ point-in-time BANK değerleme / M2 / Total Rasyo
```

Bu katman daha sonra GYO, holding, sigorta, diğer finans ve finans dışı
motorların da ortak ham veri omurgası olacaktır. Örnek KAP kodları bilerek
çalışmayan `PORTAL_DOKUMAN...` yer tutucularıdır; gerçek kodlar yalnız kayıt
olunan MKK ürün dokümanından alınmalıdır.

Migration ve örnek yapılandırmalar:

```bash
psql -f sql/016_semantic_sector_materialization.sql
cp config/kap_bank_semantic_mapping.example.json \
   config/kap_bank_semantic_mapping.json
cp config/bank_fact_derivation.example.json \
   config/bank_fact_derivation.json
```

Ham fact'leri semantic alanlara eşle:

```bash
python -m src.app.cli map-kap-semantic-facts \
  --semantic-config config/kap_bank_semantic_mapping.json \
  --source-mapping-profile KAP_FINANCIAL_FACTS \
  --source-mapping-version 1 \
  --analysis-at 2026-08-04T20:00:00+03:00
```

Semantic fact'lerden sekiz dönemlik BANK metriklerini üret:

```bash
python -m src.app.cli materialize-bank-facts \
  --derivation-config config/bank_fact_derivation.json \
  --analysis-at 2026-08-04T20:00:00+03:00 \
  --anchor 2026-06-30
```

BANK formülleri açık sözleşmedir:

- `BVPS = toplam özkaynak / dolaşımdaki pay sayısı`
- `ROE_TTM = son dört bağımsız çeyrek kârı / dönem başı-sonu ortalama özkaynak`
- YTD kârlar yalnız bir önceki takvim çeyreği mevcutsa bağımsız çeyreğe çevrilir.
- Eksik çeyrek sıkıştırılmaz; ROE ilgili dönem için kullanılamaz olur.
- Payout önce geçerli doğrudan oran, sonra TTM temettü/kâr fallback'iyle üretilir.
  Geçersiz doğrudan payout, fallback ile gizlenmez.

Fail-closed ve izlenebilirlik kuralları:

- Aynı kaynak kalem kodu yalnız currency/scope/dimension/period seçicileri
  gerçekten ayrışıyorsa birden fazla canonical alana bağlanabilir.
- Aynı öncelikte farklı değer varsa satır sırasına göre seçim yapılmaz; ret edilir.
- Point-in-time seçim `published_at`, `version_sequence`, tam lineage SHA sırasıdır.
- Semantic facts ve türetilmiş BANK satırları değer/kimlik açısından değişmezdir;
  idempotent yeniden çalıştırma yalnız işlem zamanını ileri taşıyabilir.
- Bütün türetilmiş satırlar kullanılan kaynak kalemlerin tam lineage listesini taşır.

Doğrulama:

```bash
PYTHONPATH=. pytest -q tests
PYTHONPATH=. python scripts/self_audit_semantic_bank_facts.py
PYTHONPATH=src/analytics/bank_v47 \
  pytest -q vendor/v47_roe_belirsizlik
```

## Resmî KAP XBRL → BANK → M2 → Total Rasyo tek şirket hattı

Bu sürüm, resmî KAP finansal raporlarında görülen TFRS/XBRL etiketleri için ilk
sürümlü BANK profilini içerir:

- `ifrs-full_Equity` → `TOTAL_EQUITY`
- `ifrs-full_IssuedCapital` → `ISSUED_CAPITAL`
- `ifrs-full_ProfitLossAttributableToOwnersOfParent` öncelikli,
  `ifrs-full_ProfitLoss` yedek → `NET_INCOME`
- `ifrs-full_DividendsPaid` → `DIVIDENDS_PAID`

Dosyalar:

```text
config/kap_bank_semantic_mapping.official_v1.json
config/bank_fact_derivation.official_v1.json
docs/KAP_XBRL_BANK_KANITLARI.md
```

Pay sayısı, ödenmiş sermayeden yalnız config'te açıkça verilen nominal pay değeri
ile türetilir:

```text
shares = issued_capital / share_nominal_value
```

Doğrudan `SHARES_OUT` alanı varsa önceliklidir; geçersiz doğrudan değer fallback
ile gizlenmez. İlk resmî profil `share_nominal_value = 1 TRY` varsayımını açıkça
saklar. Başka nominal değerde yeni config sürümü oluşturulmalıdır.

Veritabanından bağımsız kabul/önizleme fonksiyonu:

```python
from src.analytics.kap_bank_end_to_end import evaluate_kap_bank_end_to_end
```

Akış:

```text
Kayıpsız KAP bildirimleri
→ finansal fact çıkarımı
→ resmî semantic BANK mapping v1
→ sekiz dönem BANK metrikleri
→ sabit takvim yuvası ve point-in-time kanonik satır
→ BANK değerleme
→ BANK M2
→ ortak Total Rasyo formülü ve AL/İZLE/UZAK sonucu
```

Çıktı, skorların yanında kullanılan bildirim kimliklerini, yayın zamanlarını,
payload SHA256 değerlerini ve bütün mapping/derivation sürümlerini de taşır.
Aynı bildirim kimliğinde farklı payload veya farklı yayın kimliği, bozuk payload
hash'i ve yayın anından önce yapılmış imkânsız alım sert biçimde reddedilir.

Doğrulama komutları artık depo kökünden ek `PYTHONPATH` ayarı olmadan çalışır:

```bash
make test                 # Python entegrasyon testleri
make test-bank-v47        # saf BANK motoru
make test-all             # ikisi birlikte
make self-audit-kap-bank-e2e
```

Canlı MKK ürün JSON yolları ve API endpoint'i bu profilin parçası değildir;
portal ürün dokümanı/API anahtarı geldiğinde mevcut kayıpsız istemci config'ine
bağlanmalıdır. Canlı PostgreSQL ve canlı MKK çağrısı yapılmadan bu katman sentetik
ve resmî kamu raporu etiketlerine dayalı kabul kanıtıdır.

## KAP BANK toplu uçtan uca önizleme

Birden fazla bankanın kayıpsız KAP bildirimleri tek çağrıda hazırlanabilir,
leave-one-out sektör belirsizlik tabanı ile değerlenebilir ve Total Rasyo
sıralaması üretilebilir:

```bash
make preview-kap-bank-batch
```

Doğrudan komut:

```bash
python -m src.app.cli preview-kap-bank-batch \
  --file test_fixtures/kap_bank_batch_e2e/disclosures.jsonl \
  --contexts-config test_fixtures/kap_bank_batch_e2e/contexts.json \
  --mapping-config config/mkk_kap_financial_facts_mapping.example.json \
  --semantic-config config/kap_bank_semantic_mapping.official_v1.json \
  --derivation-config config/bank_fact_derivation.official_v1.json \
  --analysis-at 2026-05-15T20:00:00+03:00 \
  --anchor 2026-03-31
```

Bu komut PostgreSQL bağlantısı açmaz. Her banka için işlem sonucu veya kontrollü
ret, ayrıca Total Rasyo sıralaması üretir. Corpus sentetiktir; gerçek şirket
finansalı iddiası taşımaz.

PostgreSQL kabul çalıştırıcısı yalnız PostgreSQL 16 sunucusunda geçer:

```bash
make postgres-bank-acceptance
```

Çalıştırıcı günlük ve gün içi point-in-time referanslarını doğrular; varsayılan
olarak `FIXBNK` fikstür verisini başarı veya hata sonrasında temizler.

## KAP BANK batch kalıcılığı ve günlük sıralama

Toplu KAP→BANK→M2→Total Rasyo raporu tek transaction içinde PostgreSQL'e
kaydedilebilir:

```bash
make run-kap-bank-batch
```

Akış şunları aynı run kimliği altında yazar:

- `analytics.bank_valuation_periods`
- `analytics.bank_m2_scores`
- `analytics.module_scores`
- `analytics.kap_bank_batch_runs`
- `analytics.kap_bank_batch_rankings`
- `analytics.kap_bank_batch_rejections`

Aynı `analysis_at + anchor + horizon + pipeline_version` yeniden çalıştırılırsa
run kimliği değişmez. Önceki başarılı satır bu kez reddedildiyse eski
BANK/M2/sıralama sonucu aynı transaction içinde kaldırılır ve ret kaydı yazılır.
Daha eski bir gün içi koşu, daha yeni `module_scores.analysis_at` sonucunu silemez
veya üzerine yazamaz.

Son günlük banka sıralaması:

```bash
make show-bank-ranking
```

Doğrudan komut:

```bash
python -m src.app.cli show-bank-ranking \
  --asof 2026-05-15 \
  --horizon-days 63 \
  --limit 20
```

Gerekli migration:

```text
sql/017_kap_bank_batch_persistence.sql
```

Kalıcılık sınırı; sekiz dönem, disclosure/config lineage, M2 skor girdileri,
Total Rasyo katkıları, veto ve karar zinciri ile sıralama tutarlılığını
veritabanına dokunmadan önce doğrular.

Öz denetim:

```bash
make self-audit-kap-bank-persistence
```

## PostgreSQL ham KAP'tan dosyasız tüm BANK iş akışı

V8 ile dondurulmuş JSONL corpus'u zorunlu olmaktan çıktı. Aktif banka evreni,
ham KAP bildirimleri, point-in-time varsayımlar, fiyatlar ve diğer modül skorları
doğrudan PostgreSQL'den okunup mevcut toplu değerlendirme ve atomik kalıcılık
hattına bağlanabilir:

```bash
make run-kap-bank-db
```

Doğrudan komut:

```bash
python -m src.app.cli run-kap-bank-db \
  --mapping-config config/mkk_kap_financial_facts_mapping.example.json \
  --semantic-config config/kap_bank_semantic_mapping.official_v1.json \
  --derivation-config config/bank_fact_derivation.official_v1.json \
  --analysis-at 2026-05-15T20:00:00+03:00 \
  --horizon-days 63 \
  --max-context-age-days 7
```

İş akışı:

```text
core.instruments içindeki aktif BANK evreni
→ raw.kap_disclosures point-in-time bildirimleri
→ raw.kap_financial_facts üzerinden ortak anchor
→ analytics.bank_valuation_assumptions point-in-time COE/makro/rf
→ piyasa kapanış kesimine uygun fiyat
→ analiz saatinden ileri olmayan en son M1/M3/Ek modül bağlamı
→ KAP BANK toplu değerlendirme
→ leave-one-out sektör belirsizlik tabanı
→ BANK değerleme / M2 / Total Rasyo
→ V7 atomik kalıcılık ve günlük sıralama
```

Önemli zaman kuralları:

- `published_at <= analysis_at` olmayan KAP bildirimi kullanılamaz.
- Aynı gün kapanışı piyasa kapanmadan önce fiyat/modül bağlamına sızamaz.
- Hafta sonu veya tatil sonrası analizde tam takvim tarihi yerine en son uygun
  işlem günü bağlamı kullanılabilir; varsayılan azami yaş 7 gündür.
- `module_scores.analysis_at` analiz saatinden ileri olamaz. Timestamp'i olmayan
  legacy kayıt yalnız önceki yerel günlerden gelebilir.
- Diğer modül bağlamı eksik banka nihai sonuç üretemez; ancak finansalları
  geçerliyse diğer bankaların leave-one-out sektör artık örneklemine katkı
  verebilir.

Kullanılan varsayımın kapsamı, yürürlük zamanı, kaynağı, COE, makro tavan ve
risksiz faiz; kullanılan diğer modül tarihi/saati ve fiyat tarihi sonuç
lineage'ında saklanır.

Gerekli migration:

```text
sql/018_kap_bank_database_workflow.sql
```

Bu migration, ham bildirim/fact ve modül bağlamı sorgularına uygun point-in-time
indeksleri ekler.

Öz denetim:

```bash
make self-audit-kap-bank-db
# veya depo kökünden doğrudan:
python scripts/self_audit_kap_bank_db_workflow.py
```

Tam audit 10.500 senaryo çalıştırır. `--smoke` seçeneği doğrudan çalıştırma/import
sözleşmesini hızlı regresyon testinde kontrol eder.

## MKK API ürün onboarding (V10)

MKK API Portal ürün endpoint'i ve JSON alanları tahmin edilmez. Portal ürün dokümanından alınan örnek cevap önce ağsız doğrulanır:

```bash
python -m src.app.cli validate-mkk-contract \
  --api-config config/mkk_kap_endpoints.json \
  --sample private/mkk_sample_response.json \
  --checked-at 2026-08-05T02:00:00+03:00 \
  --contract-lock-out config/mkk_contract.lock.json
```

Uzun ilk geri-doldurma aralığını güvenli pencerelere bölmek için:

```bash
python -m src.app.cli plan-mkk-backfill \
  --start 2025-01-01T00:00:00+03:00 \
  --end 2026-08-05T00:00:00+03:00 \
  --max-window-hours 24 \
  --overlap-seconds 300
```

Canlı kontrol/sync sırasında config drift'ini engellemek için `--contract-lock` verilebilir. Kalıcı sync aynı `source_name + stream_name` için PostgreSQL advisory lock alır ve ikinci eşzamanlı worker'ı reddeder.

Ayrıntılar: `docs/MKK_URUN_ONBOARDING_V10.md`.

## MKK çoklu ürün onboarding ve örnek yakalama (V11)

Tek bir KAP ürünü yerine birden fazla resmî MKK ürününü aynı teslim sözleşmesiyle
hazırlamak için suite manifesti kullanılabilir:

```text
config/mkk_product_suite.example.json
```

Yetkili bir ürün çağrısından yalnız bir sayfalık ham sözleşme örneği almak için:

```bash
python -m src.app.cli capture-mkk-sample \
  --api-config config/mkk_kap_financials.json \
  --start 2026-08-05T00:00:00+03:00 \
  --end 2026-08-05T00:05:00+03:00 \
  --out private/mkk_kap_financials.sample.json \
  --metadata-out private/mkk_kap_financials.sample.meta.json
```

Örnek ve metadata dosyaları `0600` izinle yazılır. API anahtarı, auth header
değeri veya istek header'ları hiçbir çıktıya kaydedilmez. Mevcut dosyaların
değiştirilmesi için açıkça `--force` gerekir; iki dosyadan birinin kurulumu
başarısız olursa eski çift geri yüklenir.

Bir suite içindeki tüm ürünlerin config–örnek–contract-lock zincirini ağ ve
veritabanı kullanmadan doğrulamak için:

```bash
python -m src.app.cli validate-mkk-suite \
  --suite-config private/mkk_product_suite.json \
  --checked-at 2026-08-05T02:00:00+03:00 \
  --strict --require-api-keys
```

Çoklu ilk geri-doldurma planı:

```bash
python -m src.app.cli plan-mkk-suite-backfill \
  --suite-config private/mkk_product_suite.json \
  --checked-at 2026-08-05T02:00:00+03:00 \
  --start 2025-01-01T00:00:00+03:00 \
  --end 2026-08-05T00:00:00+03:00 \
  --max-window-hours 24 \
  --overlap-seconds 300 \
  --plan-out private/mkk_suite_backfill_plan.json
```

Suite katmanı:

- her ürünün config, sample ve lock SHA zincirini doğrular,
- aynı `source_name + stream_name` çiftinin iki üründe kullanılmasını reddeder,
- aynı uygulama API anahtarının birden fazla üründe paylaşılmasına izin verir,
- ürün bazlı pencere/overlap ayarlarını destekler,
- toplam planı 100.000 pencereyle sınırlar,
- gizli anahtarın varlığını raporlar ama değerini asla çıktılamaz.

Sentetik, canlıya hazır olmayan iki ürünlü kabul fikstürü:

```bash
make validate-mkk-suite-example
make plan-mkk-suite-example
make self-audit-mkk-suite
```

Ayrıntılar: `docs/MKK_COKLU_URUN_V11.md`.

## MKK çoklu ürün canlı senkronizasyonu (V12)

V11'de hazırlanan ürün suite'i artık tek komutla PostgreSQL checkpoint ve
advisory lock hattına bağlanabilir.

Hazırlık kontrolü:

```bash
python -m src.app.cli check-mkk-suite-readiness \
  --suite-config private/mkk_product_suite.json \
  --checked-at 2026-08-05T03:00:00+03:00 \
  --start 2026-08-01T00:00:00+03:00 \
  --end 2026-08-05T00:00:00+03:00
```

Canlı suite sync:

```bash
python -m src.app.cli sync-mkk-suite \
  --suite-config private/mkk_product_suite.json \
  --checked-at 2026-08-05T03:00:00+03:00 \
  --resume \
  --end 2026-08-05T03:00:00+03:00 \
  --quarantine-invalid-items
```

Her ürün kendi checkpoint ve product lock'una sahiptir; suite ayrıca üst seviye
lock alır. Karantina checkpoint'i ilerletmez. Varsayılan fail-fast davranışı
`--continue-on-error` ile değiştirilebilir. Sonuçlar
`raw.mkk_suite_sync_runs` ve `raw.mkk_suite_product_runs` tablolarına yazılır.

Ayrıntılar: `docs/MKK_SUITE_SYNC_V12.md`.

## BANK dışı semantik çekirdek ve CORE rasyolar (V13)

BANK dışındaki şirketler için sektör yönlendirme, KAP semantik kalemlerinden
sekiz takvim çeyreklik finansal türetim ve point-in-time `CORE` rasyo hattı
eklendi. `XUMAL` artık otomatik BANK sayılmaz; `FINANCIAL` ailesine yönlenir.
Eksik çeyrekler `lag4q`, `sum4q/ttm` veya YTD dönüşümünde sıkıştırılmaz.

```bash
make self-audit-nonbank-core
```

Örnek nonbank XBRL eşlemesi canlı MKK sözleşmesi değildir. Ayrıntılar:
`docs/NONBANK_SEMANTIC_V13.md`.

## V14 — NONFIN göreli değerleme

Sanayi/hizmet (`NONFIN`) şirketleri için dört bitişik çeyrek ve leave-one-out sektör emsalleriyle `PE`, `EV/EBIT`, `PS`, `PB` değerleme bandı ve iki eksenli M2 eklendi.

```bash
make run-nonfin-batch
make self-audit-nonfin-valuation
```

Teknik ayrıntılar: `docs/NONFIN_VALUATION_V14.md`.

## V15 — HOLDING NAD iskonto değerlemesi

Holding şirketleri için açık kaynaklı NAD kayıtlarına dayanan, point-in-time ve
leave-one-out emsal iskonto motoru eklendi. Muhasebe özkaynağı otomatik NAD
sayılmaz. Para birimi ve `share_basis` uyuşmazlığı fail-closed davranır.

```bash
make ingest-holding-nav
make run-holding-batch
make self-audit-holding-valuation
```

Ayrıntılar: `docs/HOLDING_VALUATION_V15.md`.

## V16 — GYO PD/NAD değerlemesi

GYO şirketleri için açık kaynak zinciri taşıyan NAD kayıtlarına dayalı,
point-in-time ve leave-one-out PD/NAD değerleme motoru eklendi. Muhasebe
özkaynağı veya yalnız portföy değeri otomatik NAD sayılmaz. Doğrudan NAD ile
bileşenlerden türetilmiş NAD ayrı kaynak güveni taşır; para birimi ve
`share_basis` uyuşmazlığı fail-closed davranır.

```bash
make ingest-gyo-nav
make run-gyo-batch
make self-audit-gyo-valuation
```

Ayrıntılar: `docs/GYO_VALUATION_V16.md`.

## V17 — Sigorta PD/DD + F/K değerlemesi

Sigorta şirketleri için `NON_LIFE` ve `LIFE_PENSION` alt gruplarını kesin
ayıran, aynı dönem ve muhasebe profilindeki leave-one-out emsallerle PD/DD ve
pozitif kâr varsa F/K bandı üreten değerleme motoru eklendi. Teknik marj,
birleşik oran ve yatırım geliri bağımlılığı fiyat bandını şişirmez; güven
katsayısını etkiler. Farklı TFRS/muhasebe profilleri karıştırılmaz.

```bash
make ingest-insurance-metrics
make run-insurance-batch
make self-audit-insurance-valuation
```

Ayrıntılar: `docs/INSURANCE_VALUATION_V17.md`.
