# BANK Entegrasyonu — Öz Denetim Raporu

Tarih: 2026-08-04

## Test sonuçları

- Entegrasyon ve köprü testleri: **82 passed**
- Saf v4.7 motor: **277 passed, 1 xfailed**
- Birinci öz denetim:
  - 5.000 geçerli kanonik satır
  - 15.000 kontrollü kanonik ret
  - 20.000 güven çarpanı kombinasyonu
- İkinci öz denetim:
  - 20.000 point-in-time varsayım varyasyonu
  - 20.000 BANK M2 bağlam varyasyonu
  - 400 gölge rapor varyasyonu
- Toplam tarama: **80.400 senaryo**
- Kontrolsüz exception: **0**

## Mutasyon testleri

Birinci katman, 4/4:

- sekiz yuvayı yediye düşürme
- `published_at::date` ile gün içi look-ahead
- `version_sequence` tie-break kaldırma
- ters SQL çıktı sırası

İkinci katman, 3/3:

- toplu `unnest` sözleşmesini kaldırma
- tek kaynaklı point-in-time fonksiyonunu değiştirme
- günlük kapanış DB kapısını kaldırma

## BNK1 regresyonu

```text
V_mid  = 6.893445618909122
v_conf = 0.8
Saf motor = 277 passed, 1 xfailed
```

## Bu turda kendi kodumda bulunan hatalar

1. Varsayım ve M2 tarihleri İstanbul gününe çevrilmeden `.date()` ile
   kesilebiliyordu.
2. `XUMAL`, geniş finans evreni olmasına rağmen BANK batch'ine yönleniyordu.
3. Negatif/geçersiz fiyat, değerleme usable değilse doğrulanmadan kalabiliyordu.
4. `price_trade_date`, analiz anındaki daily-close kesimini aşabiliyordu.
5. Floor-binding oranı gerçek sayısal bağlanma yerine `floor_source` etiketinden
   çıkarılıyordu.
6. Gölge rapor bozuk sayısal değerleri `errors="coerce"` ile gizliyordu.
7. Yakın iki shadow eşiği aynı iki ondalık sütun adını üretebiliyordu.
8. BANK M2 korunmuş olsa bile M3/momentum/volatilite/eski M2 aynı günün gelecekteki
   kapanışını görebiliyordu.
9. Kullanılan COE/makro/risksiz faiz değerleri değerleme kaydında ayrı izlenmiyordu.

Hepsi kapatıldı ve regresyon testi eklendi.

## Veritabanı ikinci savunma katmanı

Yeni CHECK kapıları:

- varsayım risksiz faiz aralığı ve JSON/source sözleşmesi
- finansal dönem sonunun yayın anından ileri olmaması
- varsayım/yayın/sektör kesiminin analiz anından ileri olmaması
- COE/makro/risksiz faiz ekonomik aralıkları
- M2 `asof_date` değerinin İstanbul yerel günüyle eşleşmesi
- daily-close tarihinin 18:30 kesimini aşmaması
- skor girdileri, tanılar ve güven çarpanlarının JSON nesnesi olması

## Canlı PostgreSQL durumu

Bu çalışma ortamında PostgreSQL istemcisi/sunucusu bulunmadığı için canlı SQL
koşusu yeniden üretilemedi. Sert kabul çalıştırıcısı altyapı yokken yeşil görünmez.
Dolayısıyla **82 Python testi ve yapısal/mutasyon denetimleri geçmiştir; canlı
PostgreSQL 16 kabulü ayrıca yapılmalıdır.**

## Görev grubu 3 — KAP/MKK adaptörü öz denetimi

### Tarama

- 5.000 geçerli resmî API bildirim normalizasyonu
- 5.000 geçerli finansal fact batch'i
- 15.000 bozuk API/fact varyasyonu
- 200 çakışan KAP evreni HTML varyasyonu
- Toplam: **25.200 senaryo**
- Kontrollü ret: **15.200**
- Kontrolsüz exception veya sessiz kabul: **0**

### Yakalanan hata desenleri

- falsy yanlış tip (`[] or {}`)
- yapılandırılmış alanı `str()` ile geçerli metne çevirme
- bozuk tarihi ilk 10 karakterle gizleme
- PostgreSQL sayısal sınırına bırakılan aşırı Decimal
- boş fact batch'ini başarı sayma
- bağımlılık dosyasında olmayan HTML parser
- kuru API komutunun gereksiz DB sürücüsü bağımlılığı
- checkpoint gerilemesi ve empty-batch tamamlanma zamanı

### İkinci savunma katmanı

`sql/015_kap_official_ingestion.sql` şu kapıları taşır:

- ham payload object/SHA/timestamp kontrolleri
- payload ve yayın zamanının değişmezliği
- fact mapping/sürüm/key/time/period/unit/numeric/dimensions kontrolleri
- fact context ve değerlerin değişmezliği
- ret defteri deneme ve zaman sırası kontrolleri

### Doğrulanmayan dış bağımlılıklar

- MKK API anahtarı ve kayıtlı ürün endpoint'i yoktur.
- Gerçek MKK payload örneği yoktur.
- Bu ortamda PostgreSQL istemcisi/sunucusu yoktur.
- Dolayısıyla sonuç adaptör sözleşmesi + sentetik test kanıtıdır; canlı sağlayıcı
  ve canlı veritabanı kanıtı değildir.

## Birleşik son durum

- Önceki BANK entegrasyon öz denetimleri: 80.400 senaryo
- Yeni KAP/MKK adaptörü öz denetimi: 25.200 senaryo
- Birleşik sentetik tarama: **105.600 senaryo**
- Kontrolsüz exception/sessiz kabul: **0**
- Python entegrasyon testleri: **166 passed**
- Saf BANK motoru: **277 passed, 1 xfailed**
- BNK1: `V_mid = 6.893445618909122`, `v_conf = 0.8`

## Görev grubu 4 — semantic mapping ve BANK fact türetimi

### Yeni tarama

- 3.000 geçerli, sıra mutasyonlu semantic disclosure eşlemesi
- 3.000 geçerli, sıra mutasyonlu 12 dönem BANK türetimi
- 12.000 bozuk raw/semantic/config/tarih/tip varyasyonu
- 1.000 eksik çeyrek sıkıştırma mutasyonu
- Toplam: **19.000 senaryo**
- Kontrollü bozuk ret: **12.000**
- Kontrolsüz exception veya sessiz kabul: **0**

### Kanıtlanan değişmezler

- Raw fact sırası semantic sonucu değiştirmiyor.
- Semantic fact sırası türetilmiş metrik veya lineage'i değiştirmiyor.
- Aynı source code farklı dimension üyeleriyle ayrışabiliyor.
- Sekiz hedef dönem kronolojik ve benzersiz.
- Kaynak lineage analiz saatinden ileri yayın içermiyor.
- `BVPS > 0`, ROE sonlu, payout varsa `[0,1]`.
- Eksik bağımsız çeyrek ROE'yi kullanılamaz yapıyor; zaman sıkıştırılmıyor.
- Türetilmiş source ID tam metric lineage hash'ine bağlı.

### Bu turda bulunan ek açıklar

- Public semantic mapper yanlış eleman tipinde `AttributeError` üretebiliyordu.
- Raw fact hash/tarih/Decimal/dimensions sözleşmesi mapper sınırında tekrar
  doğrulanmıyordu.
- Semantic DB trigger'ı source/disclosure/profile/version/canonical/lineage PK
  alanlarını açıkça karşılaştırmıyordu.
- Türetilmiş BANK satırının lineage taşıyan değerleri UPDATE ile değiştirilebilirdi.
- Semantic publication CHECK'i mapped_at öncesi beş dakikalık yayın toleransı
  bırakıyordu.

### Birleşik güncel kanıt

- BANK entegrasyon öz denetimleri: 80.400 senaryo
- KAP/MKK adaptörü: 25.200 senaryo
- Semantic/BANK fact katmanı: 19.000 senaryo
- Toplam: **124.600 senaryo**
- Python entegrasyon testleri: **242 passed**
- Saf BANK motoru: **277 passed, 1 xfailed**
- Kontrolsüz/sessiz hata: **0**

## Görev grubu 5 — resmî KAP BANK mapping ve uçtan uca Total Rasyo

### Yeni öz denetim

- 10.000 geçerli ortak Total Rasyo hesaplaması
- 500 sıra mutasyonlu ham KAP → BANK → M2 → Total Rasyo çalışması
- 7.000 bozuk modül/ağırlık/good-count varyasyonu
- 2.000 bozuk bildirim kimliği/hash/ticker/zaman/fact varyasyonu
- 500 gelecekteki restatement point-in-time kontrolü
- 500 eksik çeyrek sıkıştırma kontrolü
- Toplam: **20.500 senaryo**
- Kontrollü ret: **9.000**
- Kontrolsüz exception veya sessiz kabul: **0**

### Kanıtlanan değişmezler

- Ham bildirim sırası canonical veri, değerleme ve M2 sonucunu değiştirmiyor.
- Total Rasyo katkı toplamı ortak ağırlıklı formülle 1e-12 içinde eşleşiyor.
- M2 sonucu Total Rasyo girişine kayıpsız taşınıyor.
- Gelecekteki restatement tarihsel canonical/değerleme sonucunu değiştirmiyor.
- Eksik dönem sıkıştırılmadan canonical ROE yuvasında görünür kalıyor veya
  türetim kontrollü yetersiz veri veriyor.
- Aynı disclosure ID'nin payload veya yayın kimliği değiştirilemiyor.
- Payload içeriği kayıtlı SHA256 ile eşleşmek zorunda.
- Python ve NumPy bool tipleri modül skoru/good-count sayı alanı olamıyor.

### Bu turda yakalanan açıklar

- `numpy.bool_` Total Rasyo skoruna 0/1 diye sızabiliyordu.
- Karışık tipte bilinmeyen anahtarlar hata raporunda `sorted()` çökmesi yaratıyordu.
- Aynı disclosure ID + aynı payload + farklı yayın kimliği sessiz deduplikasyona
  girebiliyordu.
- E2E önizleme sınırı payload SHA bütünlüğünü ayrıca doğrulamıyordu.
- Depo kökünden düz pytest çalışması import yoluna bağlıydı.

### Güncel birleşik kanıt

- Önceki kayıtlı öz denetimler: **124.600 senaryo**
- Yeni KAP BANK E2E denetimi: **20.500 senaryo**
- Kayıtlı toplam: **145.100 senaryo**
- Entegrasyon: **276 passed**
- Saf BANK motoru: **277 passed, 1 xfailed**
- Kontrolsüz/sessiz hata: **0**
- BNK1: `V_mid = 6.893445618909122`, `v_conf = 0.8`

## V6 toplu KAP BANK öz denetimi

`scripts/self_audit_kap_bank_batch.py` toplam 2.600 senaryo çalıştırır:

- 200 bildirim/context sıra permütasyonu
- 200 geçerli rastgele fiyat ve diğer-modül bağlamı
- 200 tek-banka bozuk payload izolasyonu
- 2.000 kontrollü erken sınır reddi

Sonuç: sıfır kontrolsüz exception, sıfır sessiz kabul. Dondurulmuş sıralama
YKBNK → AKBNK → GARAN olarak tekrar üretildi. Bu sıralama gerçek yatırım sonucu
değil, sentetik kabul corpus'unun değişmezidir.

Restatement mutation notu: YKBNK sentetik corpus'unda RESTATED kaydı kaldırılınca
seçilen yayın zamanı, ROE serisi ve `V_mid` değişmektedir. Buna rağmen iki koşuda
da `s_valuation=1.0` doyumu oluştuğu için M2 ve Total Rasyo aynı kalabilir. Bu,
ara ürün ve lineage kontrollerinin yalnız nihai skordan daha güçlü olduğunu
tekrar doğrular.

## V7 — KAP BANK batch persistence öz denetimi

`scripts/self_audit_kap_bank_persistence.py` toplam **10.166** senaryo çalıştırır:

- 1.001 geçerli rapor, sonuç sırası ve timezone gösterimi mutasyonları
- 9.065 eksik alan, yanlış tip, bozuk sayaç, lineage, sıralama, M2 ve Total
  Rasyo zinciri mutasyonu
- 100 farklı persistence aşamasında enjekte veritabanı hatası

Kanıtlanan değişmezler:

- Aynı analiz anı farklı timezone gösterimleriyle aynı `run_key` üretir.
- Geçersiz rapor DB transaction'ına girmeden kontrollü reddedilir.
- Valuation, M2, module score ve ranking yazımları tek dış transaction içindedir.
- Enjekte DB hatası tek transaction'ı başarısız işaretler; yarım başarılı sonuç
  kanıtı üretilmez.
- Sekiz dönem, missing count, selected publication lineage ve disclosure SHA
  zinciri birlikte doğrulanır.
- M2 `score_inputs` değerleri üst M2 sonucu ile birebir eşleşir; tanılar skor
  girdilerine sızmaz.
- Total Rasyo katkıları skor×ağırlıkla, base score katkı toplamıyla, final score
  veto zinciriyle ve karar final score ile eşleşir.
- Eski intraday koşu daha yeni günlük module skorunu silemez veya overwrite
  edemez.

Sonuç:

```text
valid_reports               = 1001
controlled_invalid_reports  = 9065
injected_database_failures  = 100
uncontrolled_exceptions     = 0
silent_invalid_accepts      = 0
```

Bu turla kayıtlı birleşik sentetik senaryo sayısı **157.866** oldu.
Canlı PostgreSQL 16 persistence kanıtı değildir; bu ortamda `psql` yokluğu sert
hata olarak kaydedilmiştir.

## V8 — PostgreSQL ham KAP BANK iş akışı öz denetimi

`scripts/self_audit_kap_bank_db_workflow.py` tam modda **10.500** senaryo çalıştırır:

- 500 ham bildirim sırası mutasyonu; sentetik sıralama değişmemeli
- 5.000 modül skor/good-count tip, sonluluk ve aralık mutasyonu
- 4.000 raw KAP source/id/ticker/timestamp/JSON/SHA mutasyonu
- 1.000 tek-bankada değerlendirme bağlamı eksikliği; üç banka hazırlanmalı, iki
  sonuç üretmeli ve sonuç bankalarının sektör sample size değeri 2 kalmalı

Sonuç:

```text
scenario_count              = 10500
validated_scenarios         = 10500
uncontrolled_exceptions     = 0
silent_invalid_accepts      = 0
```

Ek kabul kanıtı:

- Dondurulmuş 37 KAP bildirimi dosya yükleyici kullanılmadan fake PostgreSQL
  cursor'larından geçirildi.
- Aktif evren, anchor, ham disclosure, varsayım, fiyat ve modül bağlamı dahil altı
  sorgu adımı çalıştı.
- Sıralama ve iki ondalık skorlar V6 corpus referansıyla eşleşti:
  `YKBNK 70.66`, `AKBNK 66.70`, `GARAN 65.98`.
- Pazartesi öğlen koşusunda Cuma bağlamının yaş sınırı içinde seçildiği; sekiz
  günden eski bağlamın reddedildiği ayrı testlerle sabitlendi.
- Audit script'inin depo kökünden doğrudan çalışması `--smoke` subprocess testiyle
  güvenceye alındı. Tam audit sayıları Make hedefinde değişmeden korunur.

Bu turla kayıtlı birleşik sentetik senaryo sayısı **168.366** oldu.
Canlı PostgreSQL 16 kanıtı değildir; bu ortamda `psql` yokluğu sert hata olarak
kaydedilmeye devam etmektedir.

### Audit çalıştırma altyapısı denetimi

V8 kapanışında eski V5–V7 audit script'lerinin yalnız Makefile'ın verdiği
`PYTHONPATH=.` ile çalıştığı, doğrudan `python scripts/...` çağrısında import
hatası verdiği bulundu. Ortak repository bootstrap eklendi. Persistence, batch
ve E2E auditleri doğrudan komutla yeniden çalıştırıldı ve sırasıyla 10.166,
2.600 ve 20.500 senaryoluk sonuçlarını değiştirmedi. Bu düzeltme audit
matematiğini değiştirmez; kanıtın çağırma biçimine bağlı kalmasını kaldırır.

## V9 — MKK canlı runtime öz denetimi

Çalıştırma:

```bash
python scripts/self_audit_mkk_runtime.py
```

Sonuç:

```text
sync_plan_valid             = 5000
sync_plan_controlled_reject = 2000
api_valid                   = 1250
api_quarantined             = 3750
config_valid                = 1750
config_controlled_reject    = 1750
persistence_complete        = 1000
persistence_quarantined     = 1000
total_scenarios             = 17500
uncontrolled                = 0
silent_accept               = 0
```

Bu turda öz denetim ve sınır incelemesiyle kapatılan gerçek açıklar:

1. Örnek `.invalid` endpoint config'i canlı çağrıya kadar ilerleyebiliyordu.
2. `Retry-After` dikkate alınmıyor ve servis istekleri için asgari aralık yoktu.
3. Tek bozuk API itemi bütün pencereyi düşürüyor, payload karantinası tutulmuyordu.
4. Kısmi/bozuk koşunun ayrı run izi ve checkpoint ilerletmeme sözleşmesi yoktu.
5. Manuel pencere yönetimi dışında checkpoint'ten güvenli resume yoktu.
6. `static_params` dinamik tarih/cursor parametrelerini sessizce ezebilirdi.
7. String olmayan JSON anahtarları ve `NaN/Infinity` isteğe/payload hash'ine
   ilerleyebilirdi.
8. Optional cursor/page parametreleri yapılandırılmış değerlerden `str()` ile
   makul görünen metne dönüşebiliyordu.
9. Config dataclass doğrudan kurulunca yalnız `static_params` yeniden
   doğrulanıyor; diğer kapılar atlanabiliyordu.
10. API cevap ve tek item boyutu sınırsızdı; beklenmeyen büyük payload belleğe
    ve veritabanına kadar ilerleyebilirdi.
11. Kaynak adı istemcide sabitti; birden fazla resmî ürün akışı aynı source
    checkpoint alanında karışabilirdi.
12. Farklı payload taşıyan duplicate kimlik quarantine modunda bile ham hata ile
    bütün batch'i durduruyordu.

Karantina modu veri kaybını gizlemez: karantina varsa checkpoint ilerlemez ve
koşu başarılı kabul edilmez.
13. Sağlık kontrolü placeholder config'i doğru reddediyor fakat CLI tam traceback
    basıyordu; bilinen config/protocol/transport hataları kontrollü mesaj oldu.

## V10 — MKK ürün onboarding öz denetimi

Yeni sınırlar:

- portal örnek cevap JSON sözleşmesi,
- config fingerprint ve contract-lock drift,
- backfill pencere/overlap ilerleme garantisi,
- PostgreSQL advisory lock edinme ve serbest bırakma,
- canlı HTTPS/hostname politikası.

Tarama sonucu:

```text
Toplam senaryo                 : 14.000
Geçerli backfill               : 5.000
Kontrollü backfill reddi       : 3.000
Geçerli contract+lock          : 2.500
Kontrollü contract reddi       : 2.500
Advisory lock kontrolü         : 1.000
Kontrolsüz exception           : 0
Sessiz bozuk kabul             : 0
```

Önemli tasarım kararı: örnek/placeholder config, örnek JSON sözleşmesini başarıyla doğrulayabilir; fakat raporda ayrıca `live_ready=false` ve açık hata nedeni bulunur. Böylece offline sözleşme doğrulaması ile canlıya hazır olma birbirine karıştırılmaz.

## V11 — MKK çoklu ürün suite / contract sample capture öz denetimi

Yeni denetim komutu:

```bash
python scripts/self_audit_mkk_suite.py
```

Sonuç:

```text
Toplam senaryo              : 10.000
Geçerli çoklu suite planı   : 4.000
Kontrollü plan reddi        : 2.000
Geçerli güvenli capture     : 1.500
Kontrollü capture reddi     : 1.500
Kontrollü suite reddi       : 1.000
Kontrolsüz exception        : 0
Sessiz bozuk kabul          : 0
Durum                       : PASS
```

Ek hata enjeksiyonları:

- ikinci temp dosyası üretim hatası,
- ikinci hedef kurulumu sırasında `os.replace` hatası,
- sample/lock SHA drift,
- yinelenen source/stream,
- bilinmeyen manifest alanı,
- geçersiz env adı,
- non-finite pencere override'ı,
- suite toplam pencere üst sınırı,
- placeholder endpoint'in ağ çağrısından önce reddi.

Capture çıktılarında API key veya request header değeri bulunmadığı ve dosya
izinlerinin `0600` olduğu test edildi.

## V12 — MKK suite sync öz denetimi

Yeni denetim:

```bash
python scripts/self_audit_mkk_suite_sync.py
```

Sonuç:

```text
Toplam senaryo                    : 13.000
Geçerli suite raporu              : 4.000
Kontrollü bozuk rapor reddi       : 4.000
Run-key politika ayrışması        : 2.000
PostgreSQL hazırlık kontrolü      : 2.000
Gerçek orkestrasyon akışı         : 1.000
Kontrolsüz exception              : 0
Sessiz bozuk kabul                : 0
Durum                             : PASS
```

Orkestrasyon enjeksiyonları:

- tamamlama,
- pencere sınırında partial,
- karantina ve checkpoint ilerlememesi,
- ilk taşıma hatası sonrası retry,
- fail-fast kontrollü hata,
- resume/up-to-date ve ağa çıkmama.

Ek kapılar:

- config-lock SHA drift,
- API source drift,
- API zaman penceresi drift,
- tamamlanmış sonuçta cursor,
- PostgreSQL 16 ve migration relation kontrolü,
- rapor kalıcılık hatasında sonucu koruma.

## V13 — NONBANK semantik ve CORE rasyo öz denetimi

```text
Toplam senaryo                 : 12.000
Geçerli şirket türetimi        : 3.000
Eksik çeyrek koruması          : 2.000
Kontrollü bozuk fact reddi     : 2.000
Sektör yönlendirme             : 2.000
Takvim-duyarlı rasyo           : 2.000
Runtime config bypass reddi    : 1.000
Kontrolsüz exception           : 0
Sessiz bozuk kabul             : 0
Durum                          : PASS
```

Yakalanan temel riskler:

- XUMAL'ın banka gibi yönlendirilmesi,
- eksik dönemlerin satır sırasıyla sıkıştırılması,
- YTD farkında gerçek önceki çeyrek yerine önceki kayıt kullanılması,
- gelecekteki bozuk/restated fact'in geçmiş sonucu zehirlemesi,
- doğrudan dataclass kurulumuyla config doğrulamasının atlanması,
- VAL oranlarının ortak CORE hatta sızması.

Ek çapraz-yol bulgusu: evren/SQL yönlendirmesi açık sektör kodunu önceliklendirirken günlük RSC yolu endeksi önce kullanıyordu. Bu tutarsızlık kapatıldı ve regresyon testi eklendi.

## V14 NONFIN değerleme öz denetimi

Toplam 15.000 senaryo üretildi. Geçerli değerlemelerde band geometrisi, güven, skor ve M2 aralıkları doğrulandı. Bozuk senaryolarda kontrolsüz exception veya sessiz `OK` görülmedi.

Öz denetim sırasında kapatılan riskler:

- doğrudan dataclass/config kurulumuyla kapı atlama,
- sahte takvim çeyrek sonu,
- farklı türetim profillerinin aynı TTM penceresine karışması,
- UTC/İstanbul takvim günü farkı,
- bayat hedef ve emsal fiyatları,
- erken raporlayan tek şirketin global anchor ile bütün evreni reddetmesi,
- detaylı sektör kodunun geniş endeks tarafından ezilmesi.

## V15 HOLDING değerleme öz denetimi

```text
Toplam senaryo                 : 15.000
Geçerli değerleme              : 5.000
Kontrollü yetersiz veri        : 2.500
Sıra değişmezliği              : 2.000
Config bypass reddi            : 1.500
Snapshot bypass reddi          : 1.500
NAV ingest sınırı reddi        : 1.500
Batch sözleşmesi               : 1.000
Kontrolsüz exception           : 0
Sessiz bozuk veri kabulü       : 0
Durum                          : PASS
```

Kontrollü yetersiz veri taramasına yetersiz emsal, bayat NAD, bayat fiyat,
düşük kaynak güveni ve para birimi uyuşmazlığı dahil edildi. Ek regresyonlarda
`share_basis` uyuşmazlığı, otoritatif yeniden çalışma, SHA/config drift,
kanonik olmayan diagnostics ve sıfır fiyat transaction başlamadan reddedildi.

## V16 GYO değerleme öz denetimi

```text
Toplam senaryo                 : 15.000
Geçerli değerleme              : 5.000
Kontrollü yetersiz veri        : 2.500
Bayat veri                     : 2.500
Sıra değişmezliği              : 2.000
Config/snapshot bypass reddi   : 3.000
Kontrollü exception            : 3.000
Kontrolsüz exception           : 0
Sessiz bozuk veri kabulü       : 0
Durum                          : PASS
```

Kapatılan temel riskler:

- muhasebe özkaynağı veya tek başına portföy değerinin NAD sayılması,
- doğrudan NAD ile bileşenlerin uzlaşmaması,
- türetilmiş NAD'nin doğrudan kaynakla aynı güveni alması,
- hedef şirketin emsal dağılımına katılması,
- bayat NAD veya fiyatın değerleme üretmesi,
- para birimi, pay bazı ve kaynak sürümü karışması,
- kalıcılık tuple'ı ile SQL sütun listesinin sessizce ayrışması,
- migration içinde yinelenen sütunun canlı PostgreSQL'e kadar ilerlemesi.

## V17 Sigorta değerleme öz denetimi

```text
Toplam senaryo                 : 15.000
Geçerli değerleme              : 5.000
Kontrollü yetersiz veri        : 2.500
Bayat/profil reddi             : 2.500
Sıra değişmezliği              : 2.000
Config/snapshot bypass reddi   : 3.000
Kontrollü exception            : 3.000
Kontrolsüz exception           : 0
Sessiz bozuk veri kabulü       : 0
Durum                          : PASS
```

Kapatılan temel riskler:

- hayat/emeklilik ile elementer sigortanın aynı emsal dağılımına karışması,
- farklı muhasebe veya metrik profillerinin karşılaştırılması,
- negatif kârın geçerli F/K sayılması,
- hedef şirketin kendi emsal medyanına katılması,
- bayat finansal veri veya fiyatla değerleme üretilmesi,
- teknik sonuç zayıfken yatırım gelirinin değer bandını doğrudan şişirmesi,
- birleşik oran üçlüsünün kısmi verilmesi,
- aynı dönem farklı kaynak belgesinin aynı metrik kimliğine çarpması,
- immutable tabloda alan değişikliğinin `inserted_at` güncellemesi gibi
  gösterilmesi,
- doğrudan dataclass kurulumu ile config/snapshot kapılarının atlanması,
- eksik bir şirketin bütün batch context'ini zehirlemesi,
- tarih-only günlük çalışmada gelecekteki sigorta M2 sonucunun sızması.
