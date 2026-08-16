# V16 — GYO PD/NAD Değerleme Motoru

## Amaç

V16, `GYO` ailesindeki şirketler için açık kaynak zinciri taşıyan net aktif değer
(NAD) verisini point-in-time fiyat ve aynı alt gruptaki emsal GYO'ların PD/NAD
çarpanlarıyla birleştirir. Muhasebe özkaynağı veya yalnız portföy değeri otomatik
olarak NAD kabul edilmez.

Resmî dayanak çerçevesi:

- SPK, Gayrimenkul Yatırım Ortaklıklarına İlişkin Esaslar Tebliği'ni III-48.1
  altında yayımlar.
- KAP'ta GYO portföylerindeki gayrimenkullere ilişkin değerleme raporları III-48.1
  kapsamında kamuya açıklanır.
- KAP'ta yayımlanmış fiyat tespit/analist raporlarında GYO değerlemesinde NAD ve
  emsal PD/NAD çarpanı kullanılan örnekler vardır.

Bu resmî çerçeve modelin veri kaynağı ve izlenebilirlik yaklaşımını destekler;
V16'daki quantile, güven ve band politikaları ise proje model kararlarıdır ve
gerçek BIST dağılımıyla ayrıca kalibre edilmelidir.

## Veri sözleşmesi

Her NAD kaydı şu alanları taşır:

- `ticker`
- `nav_asof_date`
- timezone içeren `published_at`
- `version_tag` ve `version_sequence`
- `nav_total` veya `nav_per_share`
- `shares_out`
- `share_basis`
- `currency`
- zorunlu `property_portfolio_value`
- opsiyonel `cash_and_financial_assets`, `other_assets`, `total_liabilities`
- `nav_source_method`: `DIRECT` veya `DERIVED`
- `source_confidence`
- kaynak kimliği, URI, SHA256 ve lineage
- `nav_profile` ve `nav_version`

### Doğrudan NAD

Kaynak doğrudan NAD veya pay başına NAD açıklıyorsa `DIRECT` kullanılır. Bileşenler
de verilmişse doğrudan NAD ile bileşenlerden çıkan değer yüzde 0,5 tolerans içinde
uzlaşmalıdır.

### Bileşenlerden türetilmiş NAD

Doğrudan NAD yoksa ancak aşağıdaki bileşenlerin tamamı varsa:

```text
NAD = gayrimenkul portföy değeri
    + nakit ve finansal varlıklar
    + diğer varlıklar
    - toplam yükümlülükler
```

`DERIVED` kaydı oluşturulur. Bu kayıt değerleme güveninde
`derived_nav_confidence_factor` ile ayrıca cezalandırılır.

## Değerleme matematiği

Her şirket için:

```text
NAD / pay = nav_total / shares_out
PD/NAD    = güncel fiyat / (NAD / pay)
```

Emsal grubu:

- aynı `peer_group`,
- aynı para birimi,
- aynı `share_basis`,
- aynı kaynak profil/sürümü,
- analysis time itibarıyla bilinen,
- yaş sınırlarını geçen,
- hedef şirket hariç leave-one-out

kayıtlardan oluşur.

Emsal PD/NAD dağılımının alt quantile, medyan ve üst quantile değerleri hedef
şirketin NAD/pay değeriyle çarpılarak fiyat bandı üretilir:

```text
V_low  = NAD/pay × emsal alt PD/NAD
V_mid  = NAD/pay × emsal medyan PD/NAD
V_high = NAD/pay × emsal üst PD/NAD
```

Değerleme skoru güncel fiyatın bu banda göre logaritmik konumundan üretilir.

## Güven hesabı

`v_conf` şu unsurlardan oluşur:

- emsal sayısı,
- hedef NAD kaynak güveni,
- doğrudan/türetilmiş NAD faktörü,
- hedef NAD tazeliği,
- emsallerin ortalama kaynak kalitesi,
- band genişliği.

Yetersiz emsal, bayat NAD/fiyat, para birimi veya pay bazı uyuşmazlığı, düşük
kaynak güveni ve model alanı dışındaki PD/NAD değerleri sahte `OK` yerine
kontrollü ret üretir.

## M2 bağlantısı

GYO M2 iki eksenlidir:

```text
GYO PD/NAD değerleme ekseni
+
mevcut dönemsel fiyat/band takip ekseni
```

Değerleme güveni düşükse değerleme skoru nötr 0,50'ye doğru küçültülür.
`m2_source = GYO_PD_NAV_TWO_AXIS_V1` olarak kaydedilir.

Günlük M2 override yalnız tam `analysis_at` kesiminde uygulanır. Tarih-only
çalışma gelecekte hesaplanmış GYO M2 sonucunu kullanmaz.

## PostgreSQL

Migration:

```text
sql/024_gyo_nav_valuation.sql
```

Tablolar:

- `core.gyo_nav_snapshots`
- `analytics.gyo_valuation_periods`
- `analytics.gyo_valuation_rejections`
- `analytics.gyo_m2_scores`
- `analytics.latest_gyo_m2_scores`

NAD kayıtları değişmezdir. Aynı analiz yeniden çalıştırıldığında güncel çalışma
otoritatiftir: ret alan şirketin eski başarılı değerleme/M2 satırı kalmaz; yeni
başarı eski ret kaydını aynı transaction'da temizler.

## Kullanım

Kuru NAD doğrulama:

```bash
make ingest-gyo-nav
```

Toplu GYO önizleme:

```bash
make run-gyo-batch
```

Öz denetim:

```bash
make self-audit-gyo-valuation
```

## Kapanış doğrulaması

```text
Entegrasyon testleri       : 637 passed
Saf BANK motoru            : 277 passed, 1 xfailed
GYO öz denetimi            : 15.000 / 15.000 PASS
Kontrolsüz exception       : 0
Sessiz bozuk veri kabulü   : 0
```

## V16'da ayrıca düzeltilen eski hata

`sql/023_holding_nav_valuation.sql` içindeki
`analytics.holding_valuation_periods` tablosunda `share_basis` iki kez
tanımlanmıştı. Bu hata canlı PostgreSQL'de migration'ı düşürecekti. Yinelenen
sütun kaldırıldı ve depodaki bütün `CREATE TABLE` bloklarını tarayan genel
regresyon testi eklendi.

## Dış sınırlar

V16 aşağıdakileri tamamlanmış saymaz:

- gerçek SPK/KAP değerleme raporlarından otomatik NAD çıkarımı,
- canlı PostgreSQL 16 migration/transaction koşusu,
- gerçek BIST GYO alt gruplarının ve eşiklerin kalibrasyonu,
- farklı para birimlerini dönüştürecek point-in-time kur modülü,
- bölünme/sermaye hareketlerinde `share_basis` dönüştürücüsü.
