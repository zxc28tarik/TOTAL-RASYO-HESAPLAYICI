# MKK/KAP Canlı Runtime Güvenliği — V9

Tarih: 2026-08-04

## Amaç

Resmî MKK API ürün bilgileri ve API anahtarı sağlandığında KAP bildirimlerinin
artımlı, tekrar çalıştırılabilir ve fail-closed biçimde alınmasını sağlamak.
Bu katman gerçek endpoint uydurmaz; yalnız resmî ürün config'i doldurulduktan
sonra canlı çağrı yapar.

## Yeni üretim akışı

```text
resmî endpoint config
→ check-mkk-kap sağlık kontrolü
→ checkpoint/overlap planı
→ sayfalı MKK çağrısı
→ oran sınırı + Retry-After
→ item doğrulama
→ geçerli ham bildirimler
→ bozuk item karantinası
→ sync run kaydı
→ yalnız tam batch ise checkpoint ilerletme
```

## Canlı config kapıları

`MkkKapApiConfig.validate_live_ready()` aşağıdaki örnek/placeholder değerleri
canlı çağrıdan önce reddeder:

- `.invalid`, `.example`, `.test` hostları
- `API-PORTAL-URUN`, `RESMI_DOKUMAN`, `PLACEHOLDER` işaretleri
- kullanıcı adı/parola, query veya fragment taşıyan base URL
- istemcinin yönettiği `Accept`, `User-Agent`, `Content-Type` gibi auth header adları
- aynı ada sahip dinamik parametreler
- dinamik parametreyi ezmeye çalışan `static_params`
- string olmayan JSON nesne anahtarları ve sonlu olmayan sayılar

Doğrudan dataclass kurulumu veya sonradan `object.__setattr__` ile config kapıları
atlanamaz; istemci config'i kurulum anında baştan doğrular.

## Sağlık kontrolü

```bash
python -m src.app.cli check-mkk-kap \
  --api-config config/mkk_kap_endpoints.json \
  --start 2026-08-04T00:00:00+03:00 \
  --end 2026-08-04T00:05:00+03:00 \
  --validate-items-limit 5
```

Bu komut PostgreSQL'e dokunmaz. Kimlik doğrulama, endpoint, JSON yolları,
`items_path`, ilk kayıtların kimlik/yayın zamanı sözleşmesi ve cursor tipini
kontrol eder. Sayfalamanın tamamını tüketmez ve checkpoint yazmaz.

## Artımlı resume

```bash
python -m src.app.cli sync-mkk-kap \
  --resume \
  --api-config config/mkk_kap_endpoints.json \
  --end 2026-08-05T00:00:00+03:00 \
  --stream-name financial-statements \
  --overlap-seconds 300 \
  --max-window-hours 24
```

Kurallar:

- Checkpoint başlangıcından kontrollü overlap uygulanır.
- Tek çağrı `max_window_hours` ile sınırlanır; uzun backfill parçalara ayrılır.
- Eski backfill ileri checkpoint'i geriye götüremez.
- Tamamlanmış checkpoint içinde cursor bulunursa state bozuk kabul edilir.
- `--resume` ile elle `--cursor` birlikte kullanılamaz.
- `--resume`, `--no-persist` ile kullanılamaz; checkpoint veritabanı gerektirir.

## Oran ve cevap boyutu sınırları

Config alanları:

```json
{
  "min_request_interval_seconds": 0.25,
  "max_retry_after_seconds": 60,
  "max_response_bytes": 25000000,
  "max_item_payload_bytes": 5000000
}
```

- İstekler arasında asgari süre uygulanır.
- `429/5xx` yanıtlarında geçerli `Retry-After` kullanılır ve üst sınırla kesilir.
- `Content-Length`, gerçek response byte uzunluğu, sayfa item sayısı ve tek item
  payload boyutu fail-closed kontrol edilir.
- JSON içindeki `NaN/Infinity`, string olmayan nesne anahtarları ve JSON dışı
  tipler reddedilir.

## Karantina ve checkpoint güvenliği

`--quarantine-invalid-items` açıldığında tek bozuk kayıt bütün geçerli kayıtların
kaybolmasına neden olmaz. Bozuk item şu tabloya yazılır:

```text
raw.kap_api_quarantine
```

Her sync denemesi ayrıca şurada izlenir:

```text
raw.kap_sync_runs
```

En kritik değişmez:

```text
quarantined_count > 0
→ run status = QUARANTINED
→ raw.kap_sync_state ilerlemez
→ komut exit code 2 ile başarısız sayılır
```

Bu sayede bozuk bir item görünür biçimde saklanır fakat zaman penceresi
"tam işlendi" diye işaretlenmez. Sonraki koşu aynı pencereyi yeniden görür.

## Migration

```bash
psql -f sql/019_mkk_kap_runtime_safety.sql
```

Migration şunları ekler:

- `raw.kap_sync_runs`
- `raw.kap_api_quarantine`
- durum/sayaç/payload SHA/zaman/attempt kısıtları
- stream ve son görülme indeksleri

## Öz denetim

```bash
python scripts/self_audit_mkk_runtime.py
```

V9 referans sonucu:

```text
17.500 senaryo
5.000 geçerli resume planı
2.000 kontrollü plan reddi
1.250 geçerli API sayfası
3.750 karantinaya alınan bozuk item
1.750 geçerli config
1.750 kontrollü config reddi
1.000 tam persistence
1.000 karantinalı persistence
0 kontrolsüz exception
0 sessiz bozuk kabul
```

## Dış doğrulama sınırı

Bu çalışma ortamında:

- gerçek MKK ürün endpoint'i,
- gerçek API anahtarı,
- çalışan PostgreSQL 16 sunucusu

bulunmadığı için canlı sağlayıcı çağrısı ve canlı migration koşusu yapılmamıştır.
Kod, config, CLI, migration ve sentetik/fake-DB kanıtları hazırdır.
