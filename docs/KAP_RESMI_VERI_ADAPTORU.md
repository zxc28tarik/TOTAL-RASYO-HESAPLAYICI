# Resmî KAP/MKK Veri Adaptörü — Üretim Sözleşmesi

Tarih: 2026-08-04

## Amaç

BIST evrenini ve KAP finansal bildirimlerini otomatik alırken ham veriyi
kaybetmemek, point-in-time zamanını korumak ve farklı sektör motorlarının aynı
kaynak omurgasını kullanmasını sağlamak.

## Kaynak ayrımı

- `https://kap.org.tr/tr/bist-sirketler`: şirket/ticker evreni için.
- MKK API Portal KAP ürünleri: bildirim ve finansal fact verisi için.
- KAP özet finansal sayfaları: point-in-time fact kaynağı değildir.

MKK ürün yolları belgelenmeden kodda endpoint tahmin edilmez. Çalışan config,
portal ürün dokümanı alındıktan sonra örnek dosyadan üretilir.

## Tablolar

### `raw.kap_disclosures`

Ham JSON payload ve kanonik SHA256 saklanır. Aynı `(source, disclosure_id)` için
payload veya `published_at` değişimi trigger ile reddedilir.

### `raw.kap_sync_state`

Kaynak/akış bazında cursor ve zaman penceresi saklanır. Eski bir backfill koşusu
ileri checkpoint'i geriye götüremez.

### `raw.kap_financial_facts`

Mapping profili ve sürümüyle beraber fact context, ham değer, normalize değer,
birimle ölçeklenmiş değer ve boyutlar saklanır. Fact context/değer değişmezdir.

### `raw.kap_fact_extraction_rejections`

Bozuk veya mevcut mapping ile ayrıştırılamayan payload'lar kaybolmaz. Payload
hash'i, hata nedeni, deneme sayısı ve zamanları saklanır. Mapping düzeltilince
`--retry-rejections` ile tekrar denenebilir.

## Noktasal zaman kuralları

- `published_at`, `fetched_at`, `extracted_at`: timezone içeren datetime.
- Bir bildirim alım anından beş dakikadan fazla gelecekte olamaz.
- Fact çıkarımı yayın anından önce yapılamaz; beş dakikalık saat sapması payı var.
- Tarih metinleri ilk 10 karaktere kırpılmaz. Tam ISO date veya ISO datetime
  parse edilmek zorundadır.

## Config güvenliği

`config/mkk_kap_endpoints.example.json` ve
`config/mkk_kap_financial_facts_mapping.example.json` yalnız şablondur.
`.example.invalid` endpoint'i bilerek çalışmaz. Aşağıdaki bilgiler resmî MKK ürün
dokümanından girilmelidir:

- base URL ve ürün path'i
- API key header adı
- GET/POST yöntemi
- sayfalama/cursor alanları
- bildirim alan JSON yolları
- finansal fact JSON yolları ve birim sözleşmesi

## Operasyon sırası

1. `make migrate`
2. `fetch-kap-universe` veya `sync-kap-universe`
3. `sync-mkk-kap`
4. `extract-kap-facts`
5. Mapping profiline özgü core dönüştürücüler
6. Sektör motorları ve Total Rasyo batch'i

## Canlıya geçişte zorunlu kanıtlar

- Gerçek portal sandbox/ürün örneğiyle config contract testi
- Aynı pencerenin tekrarında aynı payload SHA'ları
- Cursor checkpoint'in geriye gitmemesi
- `published_at` saatli look-ahead testi
- ORIGINAL/RESTATED seçimi için deterministik sürüm sırası
- Fact mapping sürümü değişince eski fact'lerin silinmeden yan yana kalması
- Bozuk payload'ın ret defterine gitmesi ve düzeltilmiş mapping ile başarıyla
  yeniden çıkarılması
- BIST şirket evreni satır sayısı ve ticker hash'inin günlük izlenmesi

## Bilinen açık işler

- MKK hesabı, API key ve ürün kaydı bu ortamda yok.
- Gerçek ürün endpoint/config değerleri henüz doldurulmadı.
- Resmî API'den canlı örnek payload ile mapping doğrulanmadı.
- Fact kodlarını BANK/GYO/holding/sigorta/sanayi core alanlarına eşleyen profil
  dönüştürücüleri sonraki iş paketidir.
- Canlı PostgreSQL 16 migration ve uçtan uca koşu ayrıca yapılmalıdır.

## Semantic fact ve sektör türetim katmanı

Ham KAP fact kodları sektör motorlarında doğrudan kullanılmaz. Yeni akış:

```text
raw.kap_financial_facts
→ sürümlü SemanticMappingConfig
→ core.semantic_financial_facts
→ sektör türetim profili
→ sektörün çekirdek dönemsel metrik tablosu
```

BANK ilk tüketicidir. Semantic katman şu bağlamları kaybetmeden taşır:
canonical alan, nature, dönem başlangıç/bitişi, para birimi, statement scope,
dimensions, ham fact key, mapping profili/sürümü ve SHA256 lineage.

Semantic eşleme anında yayın kaydı mapping anından ileri olamaz. Ham API/fact
çıkarım katmanındaki sınırlı saat sapması toleransı semantic katmana taşınmaz.
Semantic ve lineage taşıyan türetilmiş BANK kayıtları veritabanında immutable'dır.

Yeni komutlar:

```bash
python -m src.app.cli map-kap-semantic-facts \
  --semantic-config <config> \
  --source-mapping-profile <raw-profile> \
  --source-mapping-version <n> \
  --analysis-at <aware-ts>

python -m src.app.cli materialize-bank-facts \
  --derivation-config <config> \
  --analysis-at <aware-ts> \
  --anchor <quarter-end>
```

Gerçek BANK source code eşlemesi yalnız MKK ürün dokümanı ve örnek payload
alındıktan sonra doldurulmalıdır. Mevcut example config gerçek KAP kodu iddiası
taşımaz.

## V8 — PostgreSQL'deki ham KAP'tan doğrudan BANK çalıştırma

Resmî veri adaptörü zinciri artık dosyaya dışa aktarma gerektirmeden mevcut analiz
hattına bağlanabilir:

```text
raw.kap_disclosures
+ raw.kap_financial_facts
+ core.semantic_financial_facts / BANK türetim profili
+ point-in-time assumptions / price / module_scores
→ run-kap-bank-db
→ atomik BANK/M2/Total Rasyo persistence ve sıralama
```

Bu katman gerçek MKK endpoint'i yerine geçmez; endpoint/config ve API key hâlâ
resmî portal ürününden sağlanmalıdır. Ancak sync edilmiş ham kayıtların analiz
motoruna geçişi, point-in-time kesimleri ve tam lineage sözleşmesi artık hazırdır.

Yeni point-in-time indeksleri `sql/018_kap_bank_database_workflow.sql` içindedir.
Canlı ortamda migration sonrası `EXPLAIN (ANALYZE, BUFFERS)` ile gerçek evren
planı ve çalışma süresi ayrıca kaydedilmelidir.


## V9 — Canlı MKK runtime, resume ve karantina

Yeni `sql/019_mkk_kap_runtime_safety.sql` migration'ı her sync denemesini
`raw.kap_sync_runs` tablosunda izler ve bozuk itemleri tam payload/SHA ile
`raw.kap_api_quarantine` tablosuna yazar. Karantina bulunan koşu checkpoint'i
ilerletmez.

Canlı çağrıdan önce `check-mkk-kap` kullanılmalıdır. Örnek/placeholder endpoint,
dinamik parametre çakışması, sonlu olmayan JSON, yönetilen HTTP header çakışması,
aşırı response/item boyutu ve config dataclass bypass'ı fail-closed reddedilir.

Artımlı çalışma için `sync-mkk-kap --resume` son checkpoint'e kontrollü overlap
uygular ve tek pencereyi `--max-window-hours` ile sınırlar. Uzun backfill böylece
küçük ve tekrar çalıştırılabilir transaction'lara ayrılır. Ayrıntılı sözleşme
`docs/MKK_CANLI_RUNTIME_V9.md` dosyasındadır.
