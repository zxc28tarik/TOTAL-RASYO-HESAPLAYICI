# MKK Çoklu Ürün Canlı Senkronizasyonu — V12

Bu sürüm, V11'deki çoklu ürün config/sample/contract-lock suite'ini gerçek
çalışma orkestrasyonuna bağlar. Ürünler tek CLI çağrısında, fakat her biri kendi
point-in-time checkpoint'i ve PostgreSQL advisory lock'u ile sırayla çalışır.

## Yeni komutlar

### Canlıya hazırlık kontrolü

```bash
python -m src.app.cli check-mkk-suite-readiness \
  --suite-config private/mkk_product_suite.json \
  --checked-at 2026-08-05T03:00:00+03:00 \
  --start 2026-08-01T00:00:00+03:00 \
  --end 2026-08-05T00:00:00+03:00
```

Bu komut ağ çağrısı yapmadan şunları birlikte doğrular:

- suite config/sample/contract-lock SHA zinciri,
- ürünlerin `source_name + stream_name` benzersizliği,
- API key ortam değişkenlerinin varlığı,
- her ürünün canlı endpoint politikasına uygunluğu,
- PostgreSQL sunucusunun gerçek sürümünün 16.x olması,
- gerekli raw tablolar ve V12 suite run tablolarının bulunması,
- ürün bazlı backfill pencere planı.

Hazırlık eksikse çıktı `NOT_READY` olur ve komut exit code 2 döndürür.

### Çoklu ürün senkronizasyonu

```bash
python -m src.app.cli sync-mkk-suite \
  --suite-config private/mkk_product_suite.json \
  --checked-at 2026-08-05T03:00:00+03:00 \
  --resume \
  --end 2026-08-05T03:00:00+03:00 \
  --max-windows-per-product 4 \
  --max-product-attempts 2 \
  --quarantine-invalid-items \
  --report-out private/mkk_suite_sync_report.json
```

## Çalışma sözleşmesi

1. Suite config ve contract-lock ağ çağrısından önce yeniden doğrulanır.
2. PostgreSQL 16 ve gerekli migration tabloları zorunludur.
3. Önce suite-geneli advisory lock alınır.
4. Her ürün için ayrıca `source_name + stream_name` advisory lock alınır.
5. Resume modunda ürünün kendi checkpoint'i okunur.
6. Ürün bazlı pencere/overlap override'ı uygulanır.
7. API sonucu source, planlanan başlangıç/bitiş ve tamamlanmış cursor sözleşmesine
   karşı doğrulanır.
8. Karantina varsa geçerli kayıtlar saklanır fakat checkpoint ilerlemez.
9. Ürün sonucu `COMPLETE`, `PARTIAL`, `UP_TO_DATE`, `QUARANTINED`, `FAILED`
   veya `NOT_RUN` olarak raporlanır.
10. Suite raporu ayrı run/product tablolarına yazılır.

## Hata politikası

Varsayılan davranış fail-fast'tir. İlk `FAILED` veya `QUARANTINED` üründen sonra
kalan ürünler `NOT_RUN` olur.

```bash
--continue-on-error
```

verilirse diğer ürünler çalışmaya devam eder. Suite sonucu bu durumda genellikle
`PARTIAL` olur.

Yalnız taşıma hataları (`KapApiTransportError`) ürün bazında tekrar denenir.
Config, sözleşme, protokol, checkpoint ve veritabanı hataları retry ile
gizlenmez.

## İzlenebilirlik

Yeni migration:

```text
sql/020_mkk_suite_sync.sql
```

Yeni tablolar:

- `raw.mkk_suite_sync_runs`
- `raw.mkk_suite_product_runs`

Run kimliği şunları içerir:

- suite adı/sürümü,
- kesin başlangıç zamanı,
- istenen tarih aralığı,
- resume/continue politikası,
- pencere, retry, sayfa ve karantina ayarları,
- ürün stream adları, config SHA'ları ve ürün override'ları.

Aynı zaman ve aralık farklı çalışma politikasıyla yürütülürse aynı run kimliği
üretilmez.

## Güvenlik ve fail-closed kapıları

- Validation sonrası config veya lock değişirse ağ çağrısı yapılmaz.
- API sonucu başka source adına sahipse kalıcılaştırılmaz.
- API sonucu planlanandan farklı zaman penceresi döndürürse checkpoint ilerlemez.
- `complete=True` sonucu cursor içeremez.
- PostgreSQL 15/17 veya eksik relation ile sync başlamaz.
- Suite raporu DB'ye yazılamazsa hesaplanan rapor yine stdout ve `--report-out`
  üzerinden verilir; komut exit code 1 döndürür.
- Dışarıdan oluşturulan bütün ürünleri `NOT_RUN` olan sahte suite raporu reddedilir.

## Make hedefleri

```bash
make check-mkk-suite-readiness \
  MKK_SUITE_CONFIG=private/mkk_product_suite.json \
  MKK_SUITE_CHECKED_AT=2026-08-05T03:00:00+03:00 \
  MKK_SUITE_START=2026-08-01T00:00:00+03:00 \
  MKK_SUITE_END=2026-08-05T00:00:00+03:00

make sync-mkk-suite \
  MKK_SUITE_CONFIG=private/mkk_product_suite.json \
  MKK_SUITE_CHECKED_AT=2026-08-05T03:00:00+03:00 \
  MKK_SUITE_END=2026-08-05T03:00:00+03:00
```

`MKK_SUITE_START` verilmezse Make hedefi resume kullanır.

## Doğrulama

- Python entegrasyon testleri: 428 passed
- Saf BANK motoru: 277 passed, 1 xfailed
- V12 suite-sync öz denetimi: 13.000/13.000 PASS
- V11 suite/capture öz denetimi: 10.000/10.000 PASS

Canlı MKK endpoint/API key ve çalışan PostgreSQL 16 bu ortamda bulunmadığı için
gerçek sağlayıcı çağrısı ve canlı migration koşusu yapılmamıştır.
