# MKK Çoklu Ürün Suite ve Güvenli Örnek Yakalama — V11

Tarih: 2026-08-05

## Amaç

MKK API Portal'da birden fazla KAP ürünü kullanılabildiği için her ürünün
endpoint, alan yolları, örnek cevap, contract-lock, API key ortam değişkeni ve
checkpoint stream'i ayrı tutulmalıdır. V11 bu parçaları tek bir suite
manifestinde birleştirir; gerçek endpoint veya alan adı tahmin etmez.

## 1. Yetkili örnek cevap yakalama

Komut:

```bash
python -m src.app.cli capture-mkk-sample \
  --api-config private/mkk_financials.config.json \
  --start 2026-08-05T00:00:00+03:00 \
  --end 2026-08-05T00:05:00+03:00 \
  --out private/mkk_financials.sample.json \
  --metadata-out private/mkk_financials.sample.meta.json
```

Davranış:

- yalnız bir API sayfası çağrılır,
- config canlı güvenlik kapısından geçmeden ağ çağrısı yapılmaz,
- `items_path`, zorunlu kimlik/zaman alanları ve cursor tipi doğrulanır,
- boş sayfa contract örneği olarak kabul edilmez,
- API key ve request header'ları sample/metadata içine yazılmaz,
- sample ve metadata `0600` izinle atomik bir çift olarak kurulur,
- mevcut dosyalar `--force` olmadan değiştirilmez,
- ikinci dosya kurulamazsa önceki iki dosya geri yüklenir.

Metadata; endpoint host/path/yöntemini, config SHA256'yı, sample SHA256'yı,
yakalama zamanını ve doğrulanan item sayısını içerir. Gizli değer içermez.

## 2. Suite manifesti

Şablon:

```text
config/mkk_product_suite.example.json
```

Her etkin ürün için zorunlu alanlar:

```json
{
  "product_name": "kap_financial_disclosures",
  "config": "mkk_kap_financials.json",
  "sample": "../private/mkk_financials.sample.json",
  "contract_lock": "../private/mkk_financials.contract.lock.json",
  "api_key_env": "MKK_API_KEY",
  "stream_name": "financial_disclosures",
  "enabled": true,
  "max_window_hours": 24,
  "overlap_seconds": 300
}
```

Yollar manifest dosyasının dizinine göre çözülür. `product_name` benzersizdir.
Aynı MKK uygulama anahtarı birden fazla ürün için kullanılabilir. Buna karşılık
aynı `source_name + stream_name` çifti iki etkin üründe kullanılamaz; aksi halde
checkpoint ve advisory-lock akışları birbirine karışır.

## 3. Ağsız suite doğrulama

```bash
python -m src.app.cli validate-mkk-suite \
  --suite-config private/mkk_product_suite.json \
  --checked-at 2026-08-05T02:00:00+03:00
```

Her ürün için:

1. API config yeniden doğrulanır.
2. Contract-lock config fingerprint'iyle karşılaştırılır.
3. Sample JSON tekrar normalize edilir.
4. Sample SHA contract-lock ile karşılaştırılır.
5. `source_name + stream_name` benzersizliği doğrulanır.
6. API key ortam değişkeninin yalnız varlığı raporlanır.

`--strict`, placeholder/güvensiz endpoint'i reddeder. `--require-api-keys`, tüm
etkin ürünlerin API key ortam değişkenini zorunlu kılar. Anahtarın değeri hiçbir
rapora yazılmaz.

## 4. Çoklu backfill planı

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

Global pencere/overlap değerleri ürün bazında ezilebilir. Global değerler bütün
ürünlerde override bulunsa dahi doğrulanır; hatalı CLI parametresi sessizce
yok sayılmaz. Her ürünün pencereleri boşluksuz ve kontrollü overlap ile üretilir.
Suite toplamı 100.000 pencereyi aşarsa işlem bellek/çıktı büyümeden reddedilir.

Plan yalnız config hash, ürün/stream kimliği, key varlığı ve zaman pencerelerini
içerir. API key değeri içermez ve ağ/veritabanı çağrısı yapmaz.

## 5. Sentetik kabul fikstürü

```text
test_fixtures/mkk_suite/
```

İki sentetik ürün içerir. Endpoint'ler `.invalid` olduğu için beklenen sonuç
`live_ready=false` değeridir. Fikstür gerçek MKK sözleşmesi değildir; yalnız suite
mekaniği, sample-lock zinciri ve plan üretimini doğrular.

## 6. Öz denetim

```bash
python scripts/self_audit_mkk_suite.py
```

10.000 senaryo:

- 4.000 geçerli çoklu ürün planı,
- 2.000 kontrollü plan reddi,
- 1.500 güvenli sample capture/yazımı,
- 1.500 kontrollü bozuk sample reddi,
- 1.000 manifest/lock/stream çakışma reddi.

Kapanış ölçütü: sıfır kontrolsüz exception ve sıfır sessiz bozuk kabul.

## 7. Dış doğrulama sınırı

Gerçek MKK endpoint, ürün üyeliği, API key ve resmî JSON örneği bu ortamda yoktur.
Bu nedenle V11 gerçek API başarısı iddia etmez. Gerçek bilgiler geldiğinde sıra:

```text
capture-mkk-sample
→ validate-mkk-contract + contract-lock
→ validate-mkk-suite --strict --require-api-keys
→ plan-mkk-suite-backfill
→ check-mkk-kap / sync-mkk-kap (ürün ürün)
```
