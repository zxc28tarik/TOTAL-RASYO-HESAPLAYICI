# Çalışma Günlüğü

## 2026-08-04 — Entegrasyon başlangıcı

### Tamamlanan görev 1: üretim şeması ve point-in-time sorgusu

- `core.bank_metrics_quarterly` eklendi.
- `published_at` alanı `TIMESTAMPTZ` olarak tanımlandı.
- Eşit yayın anlarında deterministik seçim için `version_sequence` ve `record_id` sırası sabitlendi.
- `analytics.bank_valuation_periods` ara ürünleri ve güven çarpanlarını saklayacak şekilde eklendi.
- Son sekiz **takvim çeyreğini** `generate_series` ile üreten üretim sorgusu yazıldı.
- Eksik çeyrekler `LEFT JOIN` ile `NULL` korunuyor.
- Seçim sırası: `published_at DESC, version_sequence DESC, record_id DESC`.
- Filtre: `published_at <= analysis_at::timestamptz`; tarih indirgemesi yapılmıyor.

### Öz denetim notu

- “Son sekiz kayıt” kullanılmadı; yuvalar hedef çeyrekten türetiliyor.
- Sorgu `ORDER BY period_end` ile motora kronolojik seri veriyor.
- `published_at IS NULL` kayıtlar fail-closed biçimde seçilemiyor.

### Tamamlanan görev 2: kanonik dönüşüm ve iki adımlı motor hattı

- `to_canonical_row()` eklendi; satırları sessizce sıralamıyor, yanlış pencereyi ve ters sırayı reddediyor.
- Tam sekiz takvim yuvası zorunlu.
- `pd.NA`, NumPy skalerleri, `Decimal` ve DB numeric girdileri motor öncesinde Python `float/None` tipine indirgeniyor.
- Eksik yuvada sürüm/veri bulunması sözleşme ihlali sayılıyor.
- Seçilmiş kaydın `published_at > analysis_at` olması look-ahead olarak reddediliyor.
- Üretim çağrısı iki adımlı kuruldu: `estimate_roe_uncertainty()` ardından zorunlu `sd_roe` ile `bank_valuation()`.
- Güven zinciri tek fonksiyonda kuruldu: `tier_cap × payout_factor × outlier_conf_penalty × corner_conf_penalty`.
- Ara ürünleri ve nihai sonucu idempotent yazan PostgreSQL upsert eklendi.

### Öz denetim notu

- Geçici `bank_valuation_with_estimated_uncertainty` üretim hattında kullanılmıyor.
- Motorun `pd.NA`/`np.bool_` kapıları korunuyor ancak kanonik katman başarılıysa bu kapıların tetiklenmemesi gerekiyor.
- `analysis_at` timezone'suz verilirse reddediliyor; gün içi look-ahead sessizce açılamıyor.

### Tamamlanan görev 3: point-in-time banka verisi alım sınırı

- `ingest-bank-metrics` CLI komutu eklendi.
- Banka metrik CSV sözleşmesi ve örnek şablon eklendi.
- `published_at` yalnız timezone içeren zaman damgası olarak kabul ediliyor.
- Timezone içermeyen `2025-08-08T10:00:00` gibi değerler sessizce yerel saat varsayılmadan reddediliyor.
- Doğal sürüm anahtarı için benzersiz indeks eklendi; aynı açıklamanın tekrar alınması idempotent upsert ile güncellenebilir.

### Öz denetim notu

- Finansal açıklama saatini `DATE`'e indiren eski ingest yolu banka entegrasyonunda kullanılmıyor.
- `source_disclosure_id` ayrıca benzersiz tutuluyor; veri sağlayıcı kimliği varsa tekrar kayıt engelleniyor.

### Öz denetimde bulunan ve kapatılan ek hata

- Genel CSV yükleyici `CREATE TEMP TABLE ... LIKE ... INCLUDING DEFAULTS` kullanıyordu.
- `BIGSERIAL` varsayılanı geçici tabloya taşındığı için üretim sequence'i geçici COPY sırasında da tüketilebiliyordu.
- Geçici tablo artık `SELECT <sütunlar> FROM <tablo> WITH NO DATA` ile kuruluyor; default, identity ve sequence paylaşılmıyor.

### Son sertleştirme

- Geçersiz belirsizlik konfigürasyonu artık ham `ValueError` sızdırmıyor; entegrasyon sınırında `CanonicalizationError` olarak kontrollü kırılıyor.
- Veritabanına sekiz yuva, eksik sayısı, `v_conf`, payout/BVPS ve OK band geometrisi için CHECK kısıtları eklendi.
- Böylece uygulama kodu yanlışlıkla gevşese bile bozuk bir `OK` değerleme kalıcılaştırılamıyor.

### Tamamlanan görev 4: üretim sorgusuna bağlı PostgreSQL kabul çalıştırıcısı

- Point-in-time seçim mantığı `analytics.bank_point_in_time_slots(...)` SQL fonksiyonunda tek kaynağa indirildi.
- Python üretim hattı ve PostgreSQL kabul çalıştırıcısı aynı fonksiyonu çağırıyor.
- Günlük iki analiz tarihi ve gün içi 09:00/12:00/18:00 senaryolarını üretim fonksiyonunda doğrulayan sert çalıştırıcı eklendi.
- `psql`, migration veya SQL hatası durumunda çalışma başarısız olur; sessiz skip yok.
- Bu ortamda PostgreSQL istemcisi/sunucusu bulunmadığı için canlı koşu henüz yapılamadı; çalıştırma komutu devir notunda kayıtlı.

## 2026-08-04 — Görev grubu 2: toplu BANK, M2 ve Total Rasyo köprüsü

### Tamamlanan görev 5: point-in-time varsayım kaynağı

- `analytics.bank_valuation_assumptions` eklendi.
- BANK varsayılanı ve ticker bazlı ezme destekleniyor.
- Seçim yalnız `effective_at <= analysis_at` kayıtlarından yapılıyor.
- COE, `macro_cap`, opsiyonel risksiz faiz, tier cap, payout cezası ve band
  politikası ayrı ayrı saklanıyor.
- Kullanılan varsayım anı ve kaynağı değerleme kaydına yazılıyor.
- CSV alımında scope değerleri büyük harfe çevriliyor; timezone'suz zaman reddediliyor.

### Tamamlanan görev 6: bütün bankaları tek batch ile değerlendirme

- Sekiz yuvalı point-in-time sorgu `unnest + LATERAL` ile tek istemci
  çağrısında bütün bankaları getiriyor.
- Her banka için kanonik dönüşüm ayrı yapılıyor; bir bozuk banka tüm batch'i
  durdurmuyor.
- Sektör artık ölçekleri bir kez hesaplanıyor ve hedef banka leave-one-out ile
  kendi tabanından çıkarılıyor.
- BANK motoru `XUMAL` geneline değil, yalnız açık BANK sınıfı veya `XBANK`
  üyelerine yönlendiriliyor.

### Tamamlanan görev 7: BANK M2 ve Total Rasyo bağlantısı

- BANK değerleme ekseni `valuation_score`, dönem takip ekseni mevcut
  `m2_follow_score` ile birleştirildi.
- Skora giren beş alan `score_inputs` içinde; `z_val`, kaynaklar ve teşhisler
  ayrı tutuluyor.
- BANK M2 aynı günün ileriki saatinde hesaplanmış kaydı geçmiş koşuya sızdırmıyor;
  seçim `analysis_at <= exact cutoff`.
- `module_scores` artık `m2_source` ve `m2_score_inputs` saklıyor.
- BANK sonucu varsa `BANK_TWO_AXIS_V47`, yoksa dönemsel `PERIOD_M2_V3` kullanılıyor.

### Tamamlanan görev 8: ortak piyasa saati

- Günlük fiyat tablosunun yalnız DATE taşıması nedeniyle 09:00/12:00 koşusunda
  aynı gün kapanışının sızabileceği bulundu.
- `run_daily_pipeline` artık exact `analysis_at` verildiğinde tek bir
  `market_asof` hesaplıyor.
- Beta, trailing alpha, realized alpha kalibrasyonu, decile map, beklenen band,
  dönemsel M2, M3, momentum ve volatilite aynı piyasa kesimini kullanıyor.
- İstanbul saati 18:30 öncesinde en fazla önceki günlük kapanış kullanılabiliyor.

### Tamamlanan görev 9: gölge kalibrasyon raporu

- `0.80 / 0.90 / 1.00` eşikleri sert kapı açılmadan raporlanıyor.
- Sektör × dönem kırılımında usable, ret, doyma, uç değer, `justified_pb`,
  `z_val`, ROE, güven, fiyat/orta değer, COE, makro tavan ve risksiz faiz
  dağılımları üretiliyor.
- İlk uygulamada floor-binding yalnız `floor_source` etiketinden sayılıyordu;
  bu yanlıştı. Artık `sd_roe_effective ≈ sd_roe_floor` sayısal eşitliği kullanılıyor.
- Bozuk metin, bool ve sonsuz sayılar eksikmiş gibi gizlenmiyor; kontrollü ret.
- İki ondalık rapor etiketinde çakışan eşikler reddediliyor.

### Öz denetimde bulunan ve kapatılan ek hatalar

1. `analysis_at.date()` UTC gününü kullanabiliyor, İstanbul gününü yanlış
   yazabiliyordu; yerel gün zorunlu hale getirildi.
2. Negatif fiyat, değerleme unusable olduğunda doğrulanmadan teşhise geçebiliyordu.
3. Gelecek günlük kapanış tarihi uygulama içinde ancak DB'ye kadar fark
   edilmeyebilirdi; hem uygulama hem CHECK kapısı eklendi.
4. `XUMAL` geniş finans endeksi BANK motoruna yanlış yönlenebiliyordu.
5. Floor-binding oranı kaynak etiketiyle yanlış ölçülüyordu.
6. Gölge raporu bozuk sayısal hücreleri `NaN` yapıp sessizce sürdürebiliyordu.
7. Yalnız BANK fiyat kesimini korumak yeterli değildi; diğer fiyat modüllerinde
   aynı gün kapanış sızıntısı vardı. Ortak piyasa saati eklendi.

### Son doğrulama

- Entegrasyon testleri: **82 passed**
- Saf BANK motoru: **277 passed, 1 xfailed**
- Eski öz denetim: 40.000 senaryo, 0 kontrolsüz exception
- Yeni batch/M2/gölge öz denetimi: 40.400 senaryo, 0 kontrolsüz exception
- Toplam öz denetim taraması: **80.400 senaryo**
- SQL/kaynak mutasyonları: 7/7 yakalandı
- BNK1 regresyonu değişmedi: `V_mid = 6.893445618909122`, `v_conf = 0.8`

## 2026-08-04 — Görev grubu 3: resmî KAP/MKK veri omurgası

### Tamamlanan görev 10: yapılandırılabilir resmî MKK KAP istemcisi

- Portal ürün endpoint'i veya alan adı uydurulmadı; tamamı JSON config'ten gelir.
- API key header enjeksiyonu, timezone'suz yayın, gelecek yayın, cursor tekrarı,
  farklı payload'lı duplicate ID, sayfa/retry/zaman aşımı sınırları kapatıldı.
- Ham payload kanonik SHA256 ile `raw.kap_disclosures` tablosuna kayıpsız yazılır.
- Checkpoint eski backfill ile geriye gitmez.

### Tamamlanan görev 11: resmî KAP BIST şirket evreni

- KAP'ın resmî BIST şirket listesinden ticker, şirket adı ve KAP kimliği ayrılır.
- Bir satırdaki birden çok ticker korunur; aynı ticker'ın farklı şirkete bağlanması
  reddedilir.
- Minimum satır kapısı hata/boş sayfanın evreni silmesini engeller.
- CSV + metadata snapshot atomik dosya değişimi ve SHA kayıtlarıyla üretilir.

### Tamamlanan görev 12: genel finansal fact normalizer

- Ham KAP bildirimi mapping profili/sürümüyle `raw.kap_financial_facts` tablosuna
  ayrılır.
- Ham, normalize ve birimle ölçeklenmiş değer birlikte saklanır.
- Bozuk payload ret defterine yazılır; mapping düzeltildikten sonra yeniden
  denenebilir.
- Bozuk tarih kırpma, boş fact listesi, yapılandırılmış metin alanı, bool/NaN,
  aşırı sayı/birim ve gelecekteki yayın çıkarımı fail-closed hale getirildi.

### Tamamlanan görev 13: CLI ve bağımlılık ayrımı

- `fetch-kap-universe`, `sync-kap-universe`, `sync-mkk-kap`,
  `extract-kap-facts` komutları eklendi.
- `sync-mkk-kap --no-persist`, PostgreSQL/psycopg2 olmadan yüklenebilir.
- CLI'nin eski analiz modüllerini komut kullanılmadan import etmesi kaldırıldı.
- `analysis-at/start/end` timezone offset içermek zorunda.

### Öz denetimde bulunan ve kapatılan hatalar

1. `static_params = value or {}` boş listeyi sessizce kabul ediyordu.
2. Fact tarihi ilk 10 karaktere kırpılarak bozuk son ek gizlenebiliyordu.
3. Aşırı Decimal/birim değerleri PostgreSQL yazımına kadar ilerleyebiliyordu.
4. Boş fact listesi başarılı extraction sayılabiliyordu.
5. Liste/dict metin alanları `str()` ile kabul edilebiliyordu.
6. MKK bildirim ID/ticker/cursor alanlarında aynı `str()` kaçağı vardı.
7. KAP evren parser'ı requirements'ta olmayan `lxml` bağımlılığı kullanıyordu.
8. CLI kuru KAP komutu üst seviye import nedeniyle psycopg2 istiyordu.
9. Empty sync sonucu checkpoint zamanı pencere sonuna yazılabiliyordu.
10. Eski backfill ileri checkpoint'i geriye götürebiliyordu.

### Doğrulama

- Entegrasyon testleri: **166 passed** (doküman öncesi tam koşu)
- Saf BANK motoru: **277 passed, 1 xfailed**
- KAP öz denetimi: **25.200 senaryo**, 0 kontrolsüz/sessiz hata
- Canlı MKK API: kimlik bilgisi ve ürün endpoint'i olmadığı için koşulmadı
- Canlı PostgreSQL: bu ortamda psql/server bulunmadığı için koşulmadı

## 2026-08-04 — Görev grubu 4: semantik fact → BANK çekirdek metrikleri

### Tamamlanan görev 14: sürümlü semantik finansal kalem katmanı

- `raw.kap_financial_facts` ile sektör motorları arasına
  `core.semantic_financial_facts` eklendi.
- Eşleme profili ve sürümü saklanıyor; eski sürüm silinmeden yenisi yan yana
  çalışabiliyor.
- Kaynak kodu önceliği, konsolide/solo önceliği, para birimi, boyut üyesi,
  işaret ve dönem başlangıcı politikaları config sözleşmesine alındı.
- Aynı KAP kodu, yalnız seçiciler gerçekten ayrışıyorsa birden fazla canonical
  alana bağlanabiliyor. Çakışan seçiciler config aşamasında reddediliyor.
- Aynı öncelikte farklı değer varsa fiziksel satır sırasına göre seçim yapılmıyor;
  disclosure kontrollü ret alıyor.
- Her semantic fact ham fact FK'si ve tam SHA256 lineage taşıyor.

### Tamamlanan görev 15: BANK fact türetim motoru

- `BVPS = özkaynak / pay sayısı` açık formülle üretildi.
- `ROE_TTM`, son dört bağımsız çeyrek kârının dönem başı/sonu ortalama
  özkaynağa bölünmesiyle üretildi.
- YTD kârlar yalnız önceki takvim çeyreği mevcutsa bağımsız çeyreğe çevriliyor;
  eksik dönem sıkıştırılmıyor.
- Direct TTM kâr destekleniyor.
- Payout doğrudan geçerli oranı, sonra TTM temettü/kâr fallback'ini kullanıyor.
  Geçersiz doğrudan payout fallback ile gizlenmiyor.
- Point-in-time seçim `published_at`, `version_sequence`, tam lineage SHA ile
  deterministik.
- Her türetilmiş satır kullanılan bütün kaynak kalemleri ve formül teşhislerini
  saklıyor.

### Tamamlanan görev 16: toplu materialization ve CLI

- Bütün ticker'ların semantic fact'leri tek SQL sorgusuyla getiriliyor; ticker
  başına N+1 sorgu yok.
- Bir ticker'ın bozuk verisi diğer bankaların türetimini durdurmuyor.
- Başarılı metrik yazımı ve eski ret kaydının silinmesi aynı transaction'da.
- `map-kap-semantic-facts` ve `materialize-bank-facts` CLI komutları eklendi.
- Örnek mapping kodları bilerek `PORTAL_DOKUMAN...` yer tutucusu; resmî ürün
  dokümanı olmadan gerçek kod uydurulmuyor.

### Öz denetimde bulunan ve kapatılan ek hatalar

1. Testte “daha yeni restatement” diye kurulan kayıt gerçekte bir gün eskiydi;
   kod değil test beklentisi düzeltildi.
2. İlk batch sürümü ticker başına sorgu yapıyordu; tek batch sorgusuna geçirildi.
3. Geçersiz doğrudan payout, temettü fallback'iyle gizlenebiliyordu.
4. Başarılı yazım ile ret temizliği farklı transaction'larda kalabiliyordu.
5. Aynı source code'un farklı dimension üyelerine eşlenmesi gereksiz yere
   engelleniyordu; yalnız çakışan seçiciler reddedilecek hale getirildi.
6. `SemanticFactMapper` içinde dict/nesne gelince kontrollü ret yerine
   `AttributeError` oluşabiliyordu; raw fact sınırı sıkılaştırıldı.
7. Semantic fact trigger'ı PK/lineage kimlik alanlarının değişimini tam
   karşılaştırmıyordu; bütün kimlik ve değer alanları değişmez yapıldı.
8. Türetilmiş BANK satırları için DB değişmezlik trigger'ı yoktu; lineage taşıyan
   satırlarda metrik, kaynak ve formül alanları immutable yapıldı.
9. Semantic `mapped_at` veritabanı kapısı beş dakikalık ileri yayın toleransı
   taşıyordu; semantic mapping aşamasında tam `published_at <= mapped_at`
   zorunlu hale getirildi.

### Son doğrulama

- Python entegrasyon testleri: **242 passed**
- Saf BANK motoru: **277 passed, 1 xfailed**
- Yeni semantic/BANK fact öz denetimi: **19.000 senaryo**
- Tüm öz denetimler birleşik: **124.600 senaryo**
- Kontrolsüz exception veya sessiz bozuk kabul: **0**
- BNK1 regresyonu: `V_mid = 6.893445618909122`, `v_conf = 0.8`
- Canlı PostgreSQL 16 ve gerçek MKK ürün payload'ı bu ortamda hâlâ
  doğrulanmamıştır.

## 2026-08-04 — Görev grubu 5: resmî KAP BANK eşlemesi ve Total Rasyo uçtan uca

### Tamamlanan görev 17: test çalıştırma sözleşmesinin yeniden üretilebilirliği

- Depo kökünde düz `pytest -q` komutunun 16 import hatasıyla kırıldığı bulundu.
- `pytest.ini` ile `testpaths=tests` ve depo kökü Python yolu sabitlendi.
- `make test`, `make test-bank-v47` ve `make test-all` hedefleri eklendi.
- Saf BANK motoru için vendor test yolu tek, yeniden üretilebilir Makefile komutuna alındı.

### Tamamlanan görev 18: resmî KAP/XBRL BANK mapping v1

- Resmî KAP raporunda görülen `ifrs-full_Equity`,
  `ifrs-full_IssuedCapital`,
  `ifrs-full_ProfitLossAttributableToOwnersOfParent`,
  `ifrs-full_ProfitLoss` ve `ifrs-full_DividendsPaid` etiketleri sürümlü config'e alındı.
- Net kârda ortaklık sahiplerine ait kalem generic `ProfitLoss` kaleminden önce gelir.
- `1.000 TL` sunum ölçeği fact normalizer katmanında uygulanır; semantic/BANK
  katmanı ölçeklenmiş TRY değerini tüketir.
- Pay sayısı için örtük varsayım kaldırıldı. Doğrudan `SHARES_OUT` yoksa yalnız
  açık config ile `issued_capital / share_nominal_value` kullanılır.
- İlk resmî profil nominal değeri `1 TRY` olarak sürümlü biçimde saklar.

### Tamamlanan görev 19: ortak Total Rasyo ve saf KAP→Total uçtan uca çalıştırıcı

- Günlük pipeline içindeki Total Rasyo ağırlık/veto/karar matematiği
  `src/analytics/total_rasyo_score.py` içine çıkarıldı.
- Ağırlıklar sessizce normalize edilmiyor; tam anahtar kümesi ve toplam 1 şartı var.
- `evaluate_kap_bank_end_to_end()` eklendi:
  kayıpsız KAP bildirimlerinden fact, semantic, BANK metric, sekiz yuva,
  değerleme, M2 ve Total Rasyo sonucu üretir.
- Çıktı kullanılan bildirim kimliği, yayın zamanı, payload SHA256 ve bütün
  fact/semantic/derivation config sürümlerini taşır.
- Aynı disclosure ID'nin farklı payload veya farklı yayın kimliğiyle gelmesi,
  bozuk payload hash'i ve yayın anından önce imkânsız fetch sert reddedilir.

### Öz denetimde bulunan ek açıklar

1. Ortak Total Rasyo fonksiyonu `numpy.bool_` değerini 0/1 olarak kabul ediyordu.
2. Fazla ağırlık/modül anahtarları karışık tipliyse hata mesajındaki `sorted()`
   kontrolsüz `TypeError` üretebiliyordu.
3. Aynı disclosure ID ve aynı payload, fakat farklı `published_at`/kimlik alanıyla
   geldiğinde ilk kayıt sessizce seçilebiliyordu.
4. E2E sınırı payload içeriği ile verilen SHA256'nın gerçekten eşleştiğini
   doğrulamıyordu.
5. Fikstürde gelecekte yayımlanmış kayıt, yayınından önce fetch edilmiş gibi
   kurulmuştu; test verisi gerçekçi zaman sırasına çekildi.

### Son doğrulama

- Python entegrasyon testleri: **276 passed**
- Saf BANK motoru: **277 passed, 1 xfailed**
- Yeni KAP BANK E2E öz denetimi: **20.500 senaryo**, 0 kontrolsüz/sessiz hata
- Yeniden koşulan BANK entegrasyon öz denetimi: 5.000 geçerli + 15.000 kontrollü
  kanonik durum, 20.000 güven kombinasyonu, 4/4 SQL mutasyonu
- BNK1: `V_mid = 6.893445618909122`, `v_conf = 0.8`
- Kayıtlı birleşik sentetik tarama: **145.100 senaryo**
- Canlı MKK API ve canlı PostgreSQL 16 bu ortamda doğrulanmış değildir.

## 2026-08-04 — V6 toplu KAP BANK E2E başlangıcı

- Tek şirket KAP→BANK→M2→Total Rasyo yolu hazırlama/değerlendirme aşamalarına ayrıldı.
- Çoklu banka batch çalıştırıcısı eklendi; sektör artık ölçeği leave-one-out kuruluyor.
- Bir bankanın bozuk payload'ı diğer banka sonuçlarını zehirlemiyor; kontrollü ret olarak izole ediliyor.
- Dondurulmuş üç banka corpus'u eklendi: konsolide tercih edilen kâr etiketi, solo fallback etiketi, temettü eksikliği ve tarihsel RESTATED varyantları.
- PostgreSQL gerektirmeyen `preview-kap-bank-batch` CLI komutu eklendi.
- JSON raporunda `set` serileştirme açığı ve batch global disclosure kimliği yapılandırılmış tip açığı öz denetimde bulunup kapatıldı.
- PostgreSQL kabul çalıştırıcısı server_version_num ile PostgreSQL 16'yı doğruluyor, 120 saniye timeout uyguluyor ve fikstürü best-effort temizliyor.
- Son doğrulama: 291 entegrasyon testi, 277 passed + 1 xfailed saf motor, 2.600/2.600 batch öz denetim senaryosu.

## 2026-08-04 — Görev grubu 7: batch kalıcılığı ve günlük BANK sıralaması

### Tamamlanan görev 23: atomik KAP BANK batch kalıcılığı

- `sql/017_kap_bank_batch_persistence.sql` eklendi.
- Her toplu çalışma için deterministik `run_key`, rapor SHA256, pipeline sürümü,
  analiz saati, anchor, sayaçlar ve config lineage saklanıyor.
- Başarılı sonuçlar aynı transaction içinde:
  - `analytics.bank_valuation_periods`
  - `analytics.bank_m2_scores`
  - `analytics.module_scores`
  - `analytics.kap_bank_batch_rankings`
  tablolarına yazılıyor.
- Kontrollü banka retleri `analytics.kap_bank_batch_rejections` tablosunda
  saklanıyor.
- Aynı run tekrarlandığında eski sıralama/retler silinip güncel snapshot yazılıyor.
- Önceki koşuda başarılı olup bu kez reddedilen banka için stale değerleme/M2
  sonucu transaction içinde temizleniyor.

### Tamamlanan görev 24: gün içi stale-write koruması ve ranking okuma

- `module_scores` tablosuna `analysis_at` ve `source_run_key` izleri eklendi.
- Eski bir gün içi koşu daha yeni `module_scores` satırını silemez veya overwrite
  edemez.
- Günlük pipeline da `analysis_at` alanını upsert'e taşıyor; timestamp'i olmayan
  eski koşu timestamp'li daha yeni sonucu ezemiyor.
- `analytics.latest_kap_bank_batch_rankings` görünümü eklendi.
- `run-kap-bank-batch` ve `show-bank-ranking` CLI komutları eklendi.
- Saat dilimi bakımından aynı anı gösteren `20:00+03:00` ve `17:00Z` tek run
  kimliği üretiyor.

### Öz denetimde bulunan ve kapatılan ek hatalar

1. Bazı eksik valuation/M2/Total alanları transaction başladıktan sonra ham
   `KeyError` üretebiliyordu; bütün persistence sözleşmesi DB öncesine alındı.
2. Boş M2 `score_inputs` mapping'i JSON olarak geçerli görünmesine rağmen skor
   zincirini izlenemez bırakabiliyordu; tam skor-girdi sözleşmesi zorunlu oldu.
3. Total Rasyo veto bayrağı yalnız bool olarak doğrulanıyordu; artık
   `good_count → veto → final_score → decision` zinciri yeniden hesaplanıyor.
4. Aynı gün eski analiz çalıştırıldığında stale cleanup daha yeni batch kaynaklı
   `module_scores` satırını silebilirdi; cleanup `analysis_at <= current` kapısına
   bağlandı.
5. Eski analiz upsert'i daha yeni intraday module skorunu ezebilirdi; conflict
   update'e monoton zaman koşulu eklendi.
6. Değerleme diagnostics içindeki set/frozenset sırası süreçler arasında
   değişebilirdi; JSON dönüşümü deterministik sıraya alındı.
7. Run kimliği timezone gösterimine bağlı olabilirdi; kimlik malzemesi UTC'ye
   kanonikleştirildi.

### Son doğrulama

- Python entegrasyon testleri: **307 passed**
- Saf BANK motoru: **277 passed, 1 xfailed**
- Yeni persistence öz denetimi: **10.166 senaryo**
  - 1.001 geçerli/idempotent rapor
  - 9.065 kontrollü bozuk rapor reddi
  - 100 enjekte DB hatası
  - 0 kontrolsüz exception, 0 sessiz kabul
- Yeniden çalıştırılan V6 batch öz denetimi: **2.600/2.600**
- Yeniden çalıştırılan V5 KAP E2E öz denetimi: **20.500/20.500**
- BANK entegrasyon denetimi: 40.000 kombinasyon + 4/4 SQL mutasyonu, 0 kontrolsüz hata
- BNK1: `V_mid = 6.893445618909122`, `v_conf = 0.8`
- Kayıtlı birleşik sentetik tarama: **157.866 senaryo**
- Canlı PostgreSQL kabul koşusu bu ortamda `psql` bulunmadığı için exit 1 ile
  sert kırıldı; skip veya sahte başarı oluşmadı.

## 2026-08-04 — V8 PostgreSQL ham KAP BANK veritabanı iş akışı

### Tamamlanan görev 25: dosyasız ham KAP okuma ve ortak anchor

- `src/analytics/kap_bank_db_workflow.py` eklendi.
- Aktif banka evreni `core.instruments` üzerinden veya açık ticker listesiyle
  alınıyor.
- `raw.kap_disclosures` yalnız `published_at <= analysis_at` kayıtlarıyla,
  kaynak ve bildirim türü filtresiyle okunuyor.
- Ham JSON object, SHA256, farkındalıklı yayın/çekim zamanı, ticker ve tekil
  `(source, disclosure_id)` kimliği uygulama sınırında yeniden doğrulanıyor.
- Ortak son rapor dönemi `raw.kap_financial_facts` içinden mapping profil/sürümü
  ve point-in-time kesimiyle çözülüyor. Fact extraction yapılmamışsa açık ve
  kontrollü hata dönüyor.

### Tamamlanan görev 26: point-in-time BANK bağlamlarının birleştirilmesi

- COE, makro tavan ve risksiz faiz `bank_valuation_assumptions` üzerinden mevcut
  point-in-time çözümleyiciyle alınıyor.
- Fiyat bağlamı ortak piyasa kapanış kesimini kullanıyor.
- M1/M3/Ek4/Ek1/Ek9 ve `good_count_ge8`, aynı horizon için analiz saatini aşmayan
  en son uygun işlem gününden alınıyor.
- Pazartesi öğlen analizinde Pazar günü eşitliği aranıp Cuma skorlarının kaybolması
  engellendi; varsayılan `max_context_age_days=7` ile kontrollü geri bakış var.
- Timestamp'i olmayan legacy modül satırı yalnız analiz yerel gününden önceyse
  kabul ediliyor; aynı gün timestampsiz kayıt intraday look-ahead riski nedeniyle
  kullanılmıyor.
- Tek ticker için sorgunun birden fazla bağlam satırı döndürmesi sessizce ilkini
  seçmiyor; sert sözleşme hatasıdır.
- Kullanılan varsayım/fiyat/modül bağlamının kaynak tarih-saat ve değerleri sonuç
  lineage'ına yazılıyor.

### Tamamlanan görev 27: requested universe ve sektör örneklemi ayrımı

- `evaluate_kap_bank_batch_end_to_end` açık `requested_tickers` desteği aldı.
- Diğer modül bağlamı eksik bir banka nihai Total Rasyo sonucu üretemese de KAP
  finansalları geçerliyse hazırlanmış banka olarak kalıyor ve diğer bankaların
  leave-one-out sektör artık dağılımına katkı verebiliyor.
- Bağlam reddi, genel `EVALUATION_CONTEXT_MISSING` yerine asıl nedenle
  (`POINT_IN_TIME_ASSUMPTION_MISSING`, `NON_M2_MODULE_CONTEXT_*`) rapora taşınıyor.
- Sonuçlar mevcut V7 atomik persistence hattına gönderiliyor.

### Tamamlanan görev 28: CLI, indeks migration ve doğrudan audit sözleşmesi

- `run-kap-bank-db` CLI/Make hedefi eklendi.
- `sql/018_kap_bank_database_workflow.sql` ham KAP, fact ve module score
  point-in-time sorguları için fonksiyonel indeksler ekliyor.
- `self-audit-kap-bank-db` hedefi eklendi.
- Audit script'i yalnız `PYTHONPATH=.` ile değil, depo kökünden doğrudan
  çalışabilecek biçimde kendi kök yolunu kanonik olarak ekliyor.
- `--smoke` modu doğrudan script çalıştırma sözleşmesini hızlı pytest regresyonuna
  bağladı; tam audit 10.500 senaryoyu koruyor.

### V8 öz denetiminde bulunan ve kapatılan hatalar

1. Diğer modül bağlamı eksik banka tüm hazırlık evreninden çıkarılıyordu; sektör
   tabanı gereksiz küçülüyordu. Requested/prepared/result evrenleri ayrıldı.
2. Hesapta kullanılan varsayımın yürürlük zamanı, kapsamı, kaynağı ve COE/makro/rf
   değerleri DB iş akışı sonucunda izlenmiyordu; lineage'a eklendi.
3. Python ve NumPy bool değerleri modül skor/good-count alanına sayı gibi
   sızabiliyordu; ortak bool-like kapısı eklendi.
4. Pazartesi/hafta sonu analizinde modül bağlamı tam takvim tarihine eşit aranıp
   son geçerli işlem günü kaybediliyordu; azami yaş kontrollü son uygun tarih
   seçimi getirildi.
5. Tek ticker için tekrarlanan modül bağlamı sessizce ezilebilirdi; sert hata oldu.
6. Yeni audit script'i doğrudan çalıştırıldığında `src` import yolu bulunamıyordu;
   komuta bağlı test kanıtı kaldırıldı ve smoke regresyonu eklendi.

### V8 doğrulaması

- Python entegrasyon testleri: **324 passed**
- Saf BANK motoru: **277 passed, 1 xfailed**
- Yeni DB workflow öz denetimi: **10.500/10.500**
  - 500 sıra mutasyonu
  - 5.000 kontrollü modül bağlamı reddi
  - 4.000 kontrollü ham KAP satırı reddi
  - 1.000 eksik bağlam/sektör katkısı koşusu
  - 0 kontrolsüz exception, 0 sessiz kabul
- Dondurulmuş 37 bildirim, dosya okuyucu kullanılmadan altı DB sorgu adımıyla
  yeniden üretildi; sentetik sıralama yine `YKBNK → AKBNK → GARAN`.
- Canlı PostgreSQL 16 kabul koşusu bu ortamda `psql` olmadığı için hâlâ dış
  doğrulamadır ve çalıştırıcı sert exit 1 üretmektedir.

### V8 kapanışta bulunan ek test-altyapısı hatası

- Yeni DB audit'i doğrudan çalıştırılırken eski V5–V7 audit script'lerinin hâlâ
  dış `PYTHONPATH=.` bağımlılığı taşıdığı görüldü.
- `scripts/_repo_bootstrap.py` eklendi ve `src` kullanan bütün
  `self_audit_*.py` script'leri ortak kök bootstrap'ına bağlandı.
- V7 persistence, V6 batch ve V5 KAP E2E auditleri depo kökünden doğrudan tekrar
  çalıştırıldı; sırasıyla 10.166, 2.600 ve 20.500 senaryoda önceki sonuçları
  korudu.
- Bootstrap varlığı ve V8 audit'in doğrudan smoke çalışması pytest regresyonuna
  bağlandı. Bu nedenle nihai entegrasyon test sayısı **324 passed** oldu.

## 2026-08-04 — V9 canlı MKK runtime güvenliği

- PostgreSQL 16 kurulumu yeniden denendi; çalışma ortamı DNS çözümleyemediği için
  Debian depolarına ulaşılamadı. Canlı migration koşusu yapılmadı ve sahte başarı
  üretilmedi.
- `MkkKapApiConfig.validate_live_ready()` eklendi. `.invalid/.example/.test`
  endpointler, placeholder işaretleri, URL kullanıcı bilgisi/query/fragment ve
  yönetilen HTTP header çakışmaları canlı çağrıdan önce reddediliyor.
- Config dinamik parametre adları benzersiz hale getirildi; `static_params`
  bunları ezemiyor. JSON anahtarları string ve bütün sayılar sonlu olmak zorunda.
- İstemci config'i dataclass doğrudan kurulmuş veya sonradan değiştirilmiş olsa
  bile kurulumda baştan doğruluyor.
- `check-mkk-kap` komutu PostgreSQL'e dokunmadan endpoint/auth/JSON alanları ve
  ilk kayıt örneğini kontrol ediyor.
- `min_request_interval_seconds`, `Retry-After`, response byte, item byte ve
  sayfa item sınırları eklendi.
- `KapQuarantinedItem` ve karantinalı fetch sonucu eklendi. Bozuk itemler opt-in
  biçimde kaybolmadan saklanıyor; karantina varsa batch `complete=False` oluyor.
- `raw.kap_sync_runs` ve `raw.kap_api_quarantine` tablolarını ekleyen migration
  `019_mkk_kap_runtime_safety.sql` yazıldı.
- Karantinalı batch geçerli itemleri ve karantinayı aynı transaction'da yazıyor,
  fakat `raw.kap_sync_state` checkpoint'ini ilerletmiyor ve CLI exit 2 üretiyor.
- `KapSyncCheckpoint` / `KapSyncPlan` ve `sync-mkk-kap --resume` eklendi.
  Checkpoint overlap'i ve azami pencere süresiyle backfill küçük parçalara ayrılıyor.
- MKK ürün akışları `source_name` ile ayrıldı; boş batch bile doğru kaynağın
  checkpoint/run kaydını kullanıyor.
- Yeni öz denetim: 17.500 senaryo, 0 kontrolsüz exception, 0 sessiz kabul.
- Tam Python regresyonu: 371 passed. Saf BANK motoru: 277 passed, 1 xfailed.
- Son CLI denetiminde placeholder/transport/protocol hatalarının kullanıcıya tam
  traceback döndürdüğü görüldü. `check-mkk-kap`, kuru sync ve kalıcı sync bilinen
  sağlayıcı hatalarını tek satırlık kontrollü `SystemExit` mesajına çeviriyor.

## 2026-08-05 — V10 MKK ürün onboarding ve sözleşme kilidi

- MKK portal ürün örnek cevabını ağsız doğrulayan `validate-mkk-contract` komutu eklendi.
- Config ve örnek cevap SHA256 değerlerini sabitleyen, secrets içermeyen contract-lock üretildi.
- Canlı probe/sync için isteğe bağlı `--contract-lock` doğrulaması eklendi.
- Canlı endpoint'te HTTPS zorunlu, IP literal ve placeholder hostlar fail-closed yapıldı.
- Uzun backfill aralığını overlap'li güvenli pencerelere bölen `plan-mkk-backfill` eklendi.
- Aynı source/stream sync'inin iki süreçte eşzamanlı çalışması PostgreSQL advisory lock ile engellendi.
- Yeni self-audit: 14.000 senaryo, 0 kontrolsüz hata, 0 sessiz kabul.
- Regresyon: 386 entegrasyon testi; saf BANK motoru 277 passed, 1 xfailed.

## 2026-08-05 — V11 MKK çoklu ürün suite ve güvenli contract sample capture

Tamamlananlar:

- `MkkKapApiClient.capture_contract_sample()` eklendi; tek resmî API sayfasını
  contract onboarding için yakalıyor.
- Sample + metadata iki özel dosya olarak `0600` izinle, gizli anahtar/header
  değeri yazılmadan ve rollback korumasıyla kaydediliyor.
- `capture-mkk-sample` CLI komutu eklendi.
- `MkkProductSuite` manifesti, çoklu ürün config/sample/contract-lock doğrulaması,
  API key varlık kontrolü ve `source_name + stream_name` çakışma kapısı eklendi.
- `validate-mkk-suite` ve `plan-mkk-suite-backfill` CLI komutları eklendi.
- Ürün bazlı pencere/overlap override'ı ve suite toplam 100.000 pencere üst sınırı
  eklendi.
- İki ürünlü sentetik suite fikstürü ve örnek manifest eklendi.
- `scripts/self_audit_mkk_suite.py` ile 10.000 senaryolu öz denetim eklendi.
- Backfill planı çıktısı ortak `--out` yerine güvenli `--plan-out` ile ayrıldı;
  eski açık `--out` kullanımı geriye uyumlu tutuldu.

Öz denetimde bulunan ve kapatılanlar:

1. Bütün ürünler override kullanırsa bozuk global pencere/overlap parametresi
   sessizce yok sayılabiliyordu.
2. `plan-mkk-backfill` ve `capture-mkk-sample`, ortak `--out` varsayılanı yüzünden
   `data/universe_stocks.csv` hedefini yanlışlıkla kullanabilirdi.
3. Capture çiftinin ikinci geçici dosyası hazırlanamazsa ilk geçici dosya
   temizlenmeden kalabilirdi.
4. İkinci dosya kurulumu başarısız olduğunda mevcut sample/metadata çiftinin
   birlikte geri yüklenmesi garanti değildi.
5. Çok ürünlü planın toplam pencere sayısı için suite-geneli bellek/çıktı sınırı
   yoktu.
6. Manifestte yazım hatasıyla eklenen bilinmeyen alanlar sessizce yok
   sayılabiliyordu.
7. `api_key_env` için geçersiz ortam değişkeni adları kabul edilebiliyordu.
8. Aynı uygulama API anahtarının birden fazla üründe paylaşılmasının yasaklanması
   yönündeki ilk tasarım varsayımı geri alındı; bu gerçek MKK uygulama modelinde
   geçerli bir kullanım olabilir.

Doğrulama:

- Python entegrasyon testleri: **408 passed**
- Saf BANK motoru: **277 passed, 1 xfailed**
- V11 suite öz denetimi: **10.000/10.000**, 0 kontrolsüz hata, 0 sessiz kabul
- V10 onboarding öz denetimi: **14.000/14.000**, PASS
- V9 runtime öz denetimi: **17.500/17.500**, PASS

Dış sınır değişmedi: gerçek MKK endpoint/API key ve canlı PostgreSQL 16 yoktur.

## 2026-08-05 — V12 MKK çoklu ürün canlı suite sync

Tamamlananlar:

- `src/ingest/mkk_suite_sync.py` ile çoklu ürün canlı orkestrasyonu eklendi.
- PostgreSQL 16 ve gerekli relation'ları doğrulayan hazırlık kontrolü eklendi.
- Suite-geneli lock + ürün bazlı lock/checkpoint sıralı çalışma modeli kuruldu.
- `COMPLETE/PARTIAL/UP_TO_DATE/QUARANTINED/FAILED/NOT_RUN` ürün durumları eklendi.
- Fail-fast ve `--continue-on-error` politikaları eklendi.
- Taşıma hatası için sınırlı ürün retry desteği eklendi.
- Config-lock TOCTOU, API source/pencere/cursor kimlik kapıları eklendi.
- Suite run ve product run kalıcılığı için `020_mkk_suite_sync.sql` eklendi.
- `check-mkk-suite-readiness` ve `sync-mkk-suite` CLI komutları eklendi.
- Suite raporu kalıcılık hatasında kaybolmadan stdout/rapor dosyasına yazılıyor.
- 13.000 senaryolu `self_audit_mkk_suite_sync.py` eklendi.

Öz denetimde kapatılanlar:

1. Run key çalışma politikasını içermediği için aynı anda farklı retry/continue
   politikaları çakışabilirdi.
2. Validation sonrası config/lock değişimi eski SHA ile raporlanabilirdi.
3. API sonucu farklı source veya farklı zaman penceresiyle checkpoint'i yanlış
   akışa ilerletebilirdi.
4. Tamamlanmış API sonucu cursor taşıyabilirdi.
5. İkinci pencerede hata olursa ilk pencerenin ilerleme sayaçları kaybolabilirdi.
6. Suite raporu DB'ye yazılamazsa hesaplanan rapor kullanıcıya verilmeden komut
   kapanabilirdi.
7. Bütün ürünleri `NOT_RUN` olan dışarıdan üretilmiş anlamsız rapor kabul
   edilebilirdi.

Doğrulama:

- Entegrasyon: 428 passed
- Saf BANK: 277 passed, 1 xfailed
- V12 suite sync audit: 13.000/13.000 PASS
- V11 suite audit: 10.000/10.000 PASS

## 2026-08-05 — V13 BANK dışı semantik çekirdek

Tamamlananlar:

- `sector_routing.py` ile BANK/HOLDING/GYO/INSURANCE/FINANCIAL/NONFIN yönlendirmesi tek sözleşmeye toplandı.
- `XUMAL -> FINANCIAL` yapıldı; geniş finans endeksinin BANK motoruna sızması kapatıldı.
- Genel şirketler için sürümlü semantic fact -> dönemsel finansal türetici eklendi.
- YTD -> bağımsız çeyrek dönüşümü tam takvim çeyreği şartına bağlandı.
- Gelecek restatement, eksik çeyrek sıkıştırması ve geçersiz pay fallback'i kapatıldı.
- `021_company_semantic_materialization.sql` eklendi.
- Genel rasyo `QuarterSeries` takvim-duyarlı hale getirildi.
- Yalnız CORE oranları point-in-time hesaplayan `company_ratio_pipeline.py` eklendi.
- `materialize-company-facts` ve `calc-company-ratios` CLI komutları eklendi.
- 12.000 senaryolu `self_audit_nonbank_core.py` eklendi.

Öz denetimde ayrıca doğrudan dataclass kurulumuyla `from_dict` kapılarının
atlanabildiği görüldü; runtime config yeniden doğrulaması eklendi.
- Son çapraz kontrolde günlük RSC `build_sector_group_map` yolunun geniş endeksi açık `sector_code`'dan önce kullandığı görüldü; merkezi sözleşmeyle uyumlu hale getirildi. Açık `INSURANCE/GYO/...` kodu artık XUMAL gibi geniş endeks tarafından ezilmiyor.

## V14 — NONFIN göreli değerleme ve M2

- Sanayi/hizmet şirketleri için `PE`, `EV/EBIT`, `PS`, `PB` tabanlı göreli değerleme motoru eklendi.
- Dört bitişik takvim çeyreği zorunlu hale getirildi; eksik dönem sıkıştırması reddediliyor.
- Aynı sektör grubu ve aynı finansal dönem için leave-one-out emsal dağılımı kuruldu.
- Kaynak şirket metrikleri `derivation_profile/version` ile sabitlendi.
- Bayat hedef fiyatı fail-closed; bayat emsaller örneklem dışı.
- Göreli değerleme ekseni mevcut dönemsel takip ekseniyle NONFIN M2’ye bağlandı.
- Günlük M2 önceliği BANK > NONFIN > dönemsel olarak güncellendi.
- `sql/022_nonfin_relative_valuation.sql`, `run-nonfin-batch` ve öz denetim komutu eklendi.
- 518 entegrasyon testi, 277+1 BANK testi ve 15.000 senaryolu NONFIN denetimi temiz geçti.

## 2026-08-05 — V15 HOLDING NAD iskonto motoru

- Açık kaynak/SHA taşıyan NAD JSONL sözleşmesi eklendi.
- Point-in-time, leave-one-out holding iskonto bandı ve güven hesabı yazıldı.
- HOLDING iki eksenli M2 ve günlük Total Rasyo köprüsü eklendi.
- `023_holding_nav_valuation.sql`, NAV ret defteri, CLI ve Make hedefleri eklendi.
- Para birimi ve pay/fiyat bazını ayıran zorunlu `share_basis` kapısı eklendi.
- Aynı analiz yeniden çalıştığında eski başarı/ret sonuçlarının kalmaması için
  otoritatif ve atomik yeniden çalışma semantiği kuruldu.
- Config/SHA/JSON/pozitif sayı sözleşmeleri transaction öncesinde doğrulandı.
- Örnek NAV kuru alımı PostgreSQL olmadan doğrulandı.

Doğrulama: 597 entegrasyon testi, 277 BANK testi + 1 xfail ve 15.000 holding
öz-denetim senaryosu geçti.

## 2026-08-05 — V16 GYO PD/NAD değerleme motoru

- Açık kaynak/SHA taşıyan GYO NAD JSONL sözleşmesi eklendi.
- Doğrudan NAD ve bileşenlerden türetilmiş NAD ayrı yöntem/güven ile işlendi.
- Point-in-time, leave-one-out PD/NAD bandı ve güven hesabı yazıldı.
- GYO iki eksenli M2 ve günlük Total Rasyo köprüsü eklendi.
- `024_gyo_nav_valuation.sql`, GYO ret defteri, CLI ve Make hedefleri eklendi.
- Para birimi, pay/fiyat bazı, kaynak profil/sürümü ve veri tazeliği kapıları eklendi.
- Otoritatif ve atomik yeniden çalışma semantiği kuruldu.
- Python kalıcılık tuple'ı ile SQL INSERT sütun sayısını eşitleyen test eklendi.
- V15 holding migration'ındaki yinelenen `share_basis` sütunu düzeltildi.
- Tüm SQL `CREATE TABLE` bloklarını yinelenen sütun açısından tarayan regresyon
  testi eklendi.

Doğrulama: 637 entegrasyon testi, 277 BANK testi + 1 xfail ve 15.000 GYO
öz-denetim senaryosu geçti.

## 2026-08-05 — V17 Sigorta PD/DD + F/K değerleme motoru

- Sigorta şirketleri `NON_LIFE` ve `LIFE_PENSION` alt gruplarına ayrıldı.
- Aynı alt grup, dönem, muhasebe/metrik profili, para birimi ve pay bazında
  leave-one-out PD/DD ve pozitif kârda F/K değerleme bandı eklendi.
- Teknik marj, birleşik oran, ROE ve yatırım geliri bağımlılığı tanı/güven
  katmanına bağlandı; değer bandını keyfî biçimde değiştirmiyor.
- Açık kaynak belge kimliği/SHA taşıyan TTM sigorta metrik JSONL sözleşmesi ve
  immutable PostgreSQL kaydı eklendi.
- `025_insurance_valuation.sql`, ret defteri, otoritatif yeniden çalışma,
  `run-insurance-batch`, kuru alım ve Make hedefleri eklendi.
- Sigorta iki eksenli M2 günlük Total Rasyo yoluna yalnız tam `analysis_at`
  kesiminde bağlandı.
- Kaynak belge kimliği metrik kimliğine alındı; aynı dönem farklı belgeyle
  geldiğinde çakışma önlendi.
- İmmutability trigger yalnız `inserted_at` tazelemesini kabul edecek şekilde
  sertleştirildi.

Doğrulama: 689 entegrasyon testi, 277 BANK testi + 1 xfail ve 15.000 sigorta
öz-denetim senaryosu geçti.

## V18 — Banka Dışı Finansal Kuruluş Motoru

- V17 devri doğrulandı; iddia edilen 689 test yerine 686 geçti, 3 kırıldı.
- Kırılmanın sebebi pandas 3.x'te `None → NaN` dönüşümü; `sector_routing` tek
  noktadan düzeltildi (`079e095`).
- Faktoring/leasing/tüketici finansmanı motoru eklendi (`b34a05e`).
- Alım katmanı, migration 026, batch hattı eklendi (`ff4e8dd`).
- Opsiyonel alanlarda aynı eksik değer hatası üç motorda bulundu ve
  `src/utils/missing_values.py` altında merkezîleştirildi (`cb42ba0`).
- CLI/Make/öz denetim eklendi; canlı PostgreSQL testinde kalıcılık
  fonksiyonunda `with conn:` eksikliği yakalandı (`15ec963`).
- Kapanış: 860 test, beş öz denetim 15.000/15.000, BANK 277+1 xfail.

## V19 — Total Rasyo Ana Orkestratörü

- Önceki oturumda yazılan orkestratör taslağı paketlenmeden konteyner kapandığı
  için kayboldu. V18 bundle'ından yeniden klonlandı; HEAD `4e9e29b`, 77 commit,
  860 passed ve BANK 277+1 xfail bağımsız olarak yeniden ölçüldü.
- **Kayıp taslakta yanlış sözleşme tespit edildi:** `{m1:0.30, m2:0.45, m3:0.25}`
  ağırlıkları Ek4+Ek1+Ek9'u — toplam ağırlığın %30'unu — düşürüp kalan üçünü
  yeniden normalize ediyor, vetoyu da atlıyordu. Projede zaten beş yerde
  tutarlı, kanıtlanmış altı modüllü sözleşme vardı. Taslak yeniden kurulmadı;
  `compute_total_rasyo()` doğrudan kullanıldı.
- Migration `sql/027` yazıldı; canlı PostgreSQL 16.14'te 9 CHECK senaryosu
  denendi (`c64d1ab`).
- Point-in-time modül okuma eklendi; `module_scores.m2` SELECT listesine hiç
  alınmadı (çift sayım yasağı). Gelecek kayıt koruması iki katmanlı (`db69e65`).
- Motor yalıtımı ve fail-closed tek motor sahipliği eklendi. Sır redaksiyonunda
  iki gerçek açık test sırasında bulundu: `\bsecret\b` `DB_SECRET` içinde
  eşleşmiyordu ve `\S+` `Bearer xyz123` örneğinde asıl sırrı bırakıyordu
  (`f587929`).
- Altı modüllü birleştirme çekirdeği; AST testi ikinci ağırlık kümesini
  yasakladı (`b0979e5`).
- Atomik kalıcılık (`sql/028` + `aee4f5c`). **Mutasyon testi:** `with conn:`
  kaldırılınca 13 test kırıldı — V18 hatası birebir yakalandı.
- Ana orkestratör ve uçtan uca kabul testleri (`sql/029` + `35bc0c4`). Altı
  mutasyonun altısı yakalandı; biri önce zayıf çıktı (fikstürde yeni satır hep
  daha büyük olduğu için "her sütunun en büyüğünü al" hatası tesadüfen doğru
  sonuç veriyordu) ve sertleştirildi.
- Durum sözleşmeleri netleştirildi (`sql/030` + `127b1d5`):
  `COMPLETE_NO_RESULTS` eklendi — "motorlar sağlıklı ama veri yetersiz" artık
  motor arızasıyla aynı `FAILED`'a düşmüyor. `targeted_tickers` ile evren/hedef
  ayrımı ve `not_run_policy` açık sözleşmesi eklendi.
- 15.000 senaryolu hata enjeksiyonlu öz denetim (`6a6661f`). Çalıştırınca
  **öz denetimin kendi dört kusuru** bulundu: `--replay` bozuktu (kimlik→tohum
  eşlemesi `--count`'a bağlıydı), `kirik_cursor` gerçek bağlantıyı geçiriyordu,
  nöbetçi değerler rastgele ızgarada olduğu için 6 sahte pozitif üretti, ve
  eksik bileşenin `HESAP_HATASI`'na düşmesi fark edilmiyordu. Dördü de giderildi.
- Gerçek örnek koşu `persistence_status`'un tabloda `NULL` kaldığını ortaya
  çıkardı; yazımdan önce set edilecek şekilde düzeltildi (`e370f99`).
- Kapanış: 1046 tam regresyon, BANK 277+1 xfail, PostgreSQL'siz ortamda
  969 passed + **77 skipped**, orkestratör öz denetimi 15.000/15.000.
- **V19 kapanış denetimi (bağımsız ikinci ortam):** Python 3.13.5 / pandas
  2.2.3 ortamında tam regresyon `968 passed, 77 skipped, 1 failed` verdi.
  `test_pandas_none_gercekten_nan_olur`, pandas 3.x'in fiziksel eksik-değer
  temsilini bütün `pandas>=2.2` için sözleşme sanıyordu. Ürün davranışı
  doğruydu; kusur testteydi. Hata pandas 2.2.3 sanal ortamında birebir
  yeniden üretildi, test sürümden bağımsız hale getirildi, AST koruma testi
  eklendi (`6bfaca9`). İki pandas sürümünde de doğrulandı.
- **V19 kanıt tutarsızlığı (bağımsız denetim):** kapanış belgeleri pandas 2.2.3
  için `1050 passed / 973 passed` yazıyordu; bunlar `6bfaca9` ile eklenen iki
  taşınabilirlik testinden ÖNCEKİ ölçümdü. HEAD `d92e6a1` üzerinde dört
  kombinasyon yeniden ölçüldü: her iki pandas sürümünde PG'li **1052 passed**,
  PG'siz **975 passed, 77 skipped**. Belgeler gerçek sayılara getirildi.
  Ayrıca `skipped` sayısının psycopg2'nin varlığına göre 2 veya 77 görünmesinin
  nedeni belgelendi (M2 köprü testleri `sys.modules`'e sahte psycopg2 koyuyor);
  üç durumda da `passed` 975'tir ve hiçbir test sahte bağlantıya karşı geçmez.

## V20 — Change-impact

- Bağımlılık haritası altı motor ve modül hattı için kaynak koddan çıkarıldı.
  `shares_out` provenance'ı motora göre farklı: NONFIN'de finansal çeyrek
  sütunu, HOLDING/GYO'da NAV raporu alanı. HOLDING/GYO'da V20 tetikleyici
  kenar **sıfır**.
- **CASH_FLOW kapsam bulgusu:** üç tablo tetikleyici olarak kabul ediliyor
  ama hiçbir motor nakit akım kalemi tüketmiyor. Uydurulmuş bağımlılık
  eklenmedi; boş plan `NO_SCORING_DEPENDENCY` neden koduyla dönüyor ve bu
  "kapsam dışı kaynak" ile ayrı tutuluyor.
- Off-by-one düzeltildi: n anchor = 0..n-1 offset (TTM_4Q 4/3, SERIES_8Q 8/7).
- Emsal yayılımı hedef bazlı leave-one-out ve `eligible_before ∪ after`.
- Readiness bariyeri: modülün gerçekten tazelendiği lineage ile kanıtlanmadan
  V19 çalıştırılmıyor. "Eski olmak" tek başına sorun değil; yalnız planın
  etkilendi dediği girdinin bayat kalması engelleniyor.
- Üretim rol ayrımı: runtime rolü `UPDATE/DELETE/TRUNCATE` yapamıyor.
  `TRUNCATE`'in satır trigger'larını atlaması bu yüzden kapatıldı.
- Üç ayrı kanıt zinciri: V19 15.000/15.000 (dokunulmadı), V20 15.000/15.000,
  E2E 13/13.
