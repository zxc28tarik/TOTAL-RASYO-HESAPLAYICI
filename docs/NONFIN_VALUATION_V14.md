# NONFIN Göreli Değerleme ve M2 V14

Bu sürüm yalnız `NONFIN` sektör ailesi için sanayi/hizmet şirketlerinde göreli değerleme üretir. BANK, HOLDING, GYO, INSURANCE ve FINANCIAL aileleri bu motora girmez.

## Veri sözleşmesi

- Tam dört bağımsız ve bitişik takvim çeyreği zorunludur.
- TTM gelir, EBIT ve net kâr eksik çeyrek sıkıştırılmadan hesaplanır.
- Son bilanço dönemi özkaynak, net borç ve pay sayısı için kullanılır.
- Fiyat, İstanbul takvimine göre point-in-time kesiminden gelir.
- Kaynak `derivation_profile/version` config ile sabitlenir; farklı türetim sürümleri karışamaz.
- Varsayılan çalışmada her şirket kendi son finansal dönemini kullanır. Emsaller yalnız aynı `anchor_period_end` ve aynı `peer_group` içinde karşılaştırılır. Açık `--anchor` ortak dönem zorlar.

## Çarpanlar

- `PE = piyasa değeri / TTM net kâr`
- `EV_EBIT = işletme değeri / TTM EBIT`
- `PS = piyasa değeri / TTM gelir`
- `PB = piyasa değeri / özkaynak`

Negatif veya sıfır paydalı çarpan kullanılmaz. Hedef şirket emsal örneklemine girmez (leave-one-out).

Her kullanılabilir çarpan için emsal alt quantile, medyan ve üst quantile değerlerinden hedef şirkete ima edilen fiyatlar hesaplanır. Çarpan fiyatları config ağırlıklarıyla log-geometrik olarak birleştirilir.

## Güven

`v_conf` şu bileşenlerden oluşur:

- kullanılabilir çarpan ağırlığı,
- emsal örneklem büyüklüğü,
- değerleme bandı genişliği.

Bayat hedef fiyatı değerlemeyi `YETERSIZ_VERI` yapar. Bayat emsaller örneklemden çıkarılır. Çok geniş band gölge modda tanı olarak kalır; sert modda `BAND_TOO_WIDE` döner.

## M2

NONFIN M2 iki eksenlidir:

- göreli değerleme ekseni,
- mevcut dönemsel fiyat/band takip ekseni.

Değerleme ekseni güvene göre nötr `0,5` değerine küçültülür. Varsayılan eksen ağırlıkları `0,60 / 0,40` olup config içindedir. Günlük Total Rasyo hattında öncelik:

1. BANK iki eksenli M2,
2. NONFIN göreli iki eksenli M2,
3. dönemsel M2.

BANK ve NONFIN override’ları yalnız tam `analysis_at` kesimi verildiğinde okunur.

## Komutlar

```bash
make run-nonfin-batch
make self-audit-nonfin-valuation
```

Üretim örneği:

```bash
python -m src.app.cli run-nonfin-batch \
  --analysis-at 2026-08-05T20:00:00+03:00 \
  --valuation-config config/nonfin_valuation.relative_v1.json \
  --routing-config config/sector_routing.v1.json
```

Migration:

```text
sql/022_nonfin_relative_valuation.sql
```

Tablolar:

- `analytics.nonfin_valuation_periods`
- `analytics.nonfin_m2_scores`
- `analytics.latest_nonfin_m2_scores`

## Sınır

Bu sürüm gerçek BIST emsal dağılımıyla kalibre edilmiş nihai katsayı iddiasında değildir. Quantile, minimum emsal, band genişliği ve M2 ağırlıkları gerçek veri gölge raporuyla kalibre edilmelidir.
