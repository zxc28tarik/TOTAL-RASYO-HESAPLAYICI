# MKK KAP Ürün Onboarding ve Sözleşme Kilidi — V10

Tarih: 2026-08-05

## Amaç

MKK API Portal'daki KAP ürün endpoint'i ve JSON alanları herkese açık sayfalarda yayımlanmadığı için bu proje hiçbir endpoint veya alan yolunu tahmin etmez. MKK'nın kamuya açık açıklamasına göre portalda 12 KAP veri yayın servisi vardır; kullanım için hesap onayı, API anahtarı ve ilgili ürüne kayıt gerekir.

Resmî kaynaklar:

- https://www.mkk.com.tr/haberler/mkk-api-portal-yayinda
- https://www.mkk.com.tr/online-entegrasyon-erisim-bilgileri
- https://apiportal.mkk.com.tr

Bu katman, portal ürün dokümanı veya örnek cevabı elde edildiğinde sözleşmeyi ağ bağlantısı olmadan doğrular ve değişmez bir `contract lock` üretir.

## 1. Endpoint config hazırlığı

Başlangıç dosyası:

```text
config/mkk_kap_endpoints.example.json
```

Aşağıdaki alanlar yalnız MKK API Portal'da kayıt olunan ürünün resmî dokümanından doldurulmalıdır:

- `base_url`
- `path`
- `api_key_header`
- GET/POST yöntemi
- başlangıç/bitiş parametreleri
- cursor ve sayfalama alanları
- `items_path`
- bildirim kimliği/yayın zamanı ve isteğe bağlı alan yolları

Canlı çağrı güvenlik şartları:

- HTTPS zorunlu.
- IP literal endpoint reddedilir.
- `.invalid`, `.example`, `.test`, localhost ve placeholder metinler reddedilir.
- API anahtarı config veya contract-lock içine yazılmaz.

## 2. Portal örnek cevabını doğrulama

Portal dokümanından veya yetkili test çağrısından alınan JSON cevap dosyaya kaydedilir. Ardından:

```bash
python -m src.app.cli validate-mkk-contract \
  --api-config config/mkk_kap_endpoints.json \
  --sample private/mkk_sample_response.json \
  --checked-at 2026-08-05T02:00:00+03:00 \
  --validate-items-limit 100 \
  --contract-lock-out config/mkk_contract.lock.json
```

Doğrulananlar:

- `items_path` gerçekten listeye çıkıyor mu?
- zorunlu bildirim kimliği ve timezone içeren yayın zamanı var mı?
- isteğe bağlı ticker, şirket kimliği, tür, konu ve URL kapsaması
- aynı bildirim kimliğinde farklı payload çakışması
- cursor sözleşmesi
- config ve örnek cevap SHA256 değerleri
- config'in canlı çağrıya hazır olup olmadığı

`status=OK`, yalnız örnek JSON'un config ile uyumlu olduğunu gösterir. `live_ready=false` ise placeholder veya güvenli olmayan endpoint nedeniyle gerçek çağrı yapılamaz.

## 3. Contract-lock kullanımı

Üretilen lock şunları saklar:

- config SHA256
- örnek cevap SHA256
- endpoint host/path/yöntem özeti
- parametre ve JSON alan sözleşmesi
- örnek cevap doğrulama istatistikleri

API anahtarı veya statik parametre değerleri lock içine yazılmaz.

Canlı kontrol ve sync sırasında lock doğrulaması:

```bash
python -m src.app.cli check-mkk-kap \
  --api-config config/mkk_kap_endpoints.json \
  --contract-lock config/mkk_contract.lock.json \
  --start 2026-08-05T00:00:00+03:00 \
  --end 2026-08-05T00:05:00+03:00
```

```bash
python -m src.app.cli sync-mkk-kap \
  --resume \
  --api-config config/mkk_kap_endpoints.json \
  --contract-lock config/mkk_contract.lock.json \
  --end 2026-08-06T00:00:00+03:00
```

Config sonradan değiştirilirse fingerprint uyuşmaz ve çağrı ağa çıkmadan reddedilir.

## 4. İlk geri-doldurma planı

Uzun tarih aralığı doğrudan tek API çağrısına verilmez:

```bash
python -m src.app.cli plan-mkk-backfill \
  --start 2025-01-01T00:00:00+03:00 \
  --end 2026-08-05T00:00:00+03:00 \
  --max-window-hours 24 \
  --overlap-seconds 300 \
  --out private/mkk_backfill_plan.json
```

Her pencere:

- timezone içerir,
- maksimum çağrı süresini aşmaz,
- kontrollü overlap ile sınırdaki bildirimlerin kaçmasını engeller,
- sonsuz döngü yaratacak overlap değerlerini reddeder.

Overlap nedeniyle bir sonraki pencere öncekinin sonundan geriye başlar. Bu bilinçli bir yeniden-okumadır; ham bildirim tablosu `source + disclosure_id` ile idempotenttir.

## 5. Eşzamanlı çalışma koruması

Kalıcı `sync-mkk-kap` komutu, checkpoint okunmadan önce PostgreSQL session advisory lock alır:

```text
total_rasyo:kap_sync:<source_name>:<stream_name>
```

Aynı ürün/stream için ikinci worker:

```text
MKK KAP sync zaten calisiyor
```

hatasıyla reddedilir. Farklı ürün akışları farklı `source_name` veya `stream_name` ile paralel çalışabilir.

## 6. İlk canlı devreye alma sırası

1. MKK API Portal hesabı ve ürün kaydı tamamlanır.
2. Gerçek config doldurulur.
3. Portal örnek cevabı kaydedilir.
4. `validate-mkk-contract` ile lock oluşturulur.
5. `check-mkk-kap --contract-lock` çalıştırılır.
6. Kısa bir `sync-mkk-kap --no-persist` denenir.
7. PostgreSQL migration'ları çalıştırılır.
8. Küçük bir kalıcı pencere çekilir ve ham payload sayıları kontrol edilir.
9. Backfill planı pencere pencere uygulanır.
10. Fact extraction → semantic mapping → BANK materialization → DB batch sıralaması çalıştırılır.

## 7. Bu aşamada bilinçli olarak yapılmayanlar

- MKK ürün endpoint'i tahmin edilmedi.
- API key header adı tahmin edilmedi.
- JSON alan yolları web sayfasından tersine mühendislikle uydurulmadı.
- Gerçek anahtar olmadan canlı başarı iddia edilmedi.
