# NONBANK semantik çekirdek ve takvim-duyarlı rasyo hattı — V13

## Amaç

BANK dışındaki BIST şirketlerini tek bir banka formülüne zorlamadan, ham KAP
finansal kalemlerini sektör ailesi açıkça belirlenmiş dönemsel çekirdek
finansallara dönüştürmek ve yalnız `CORE` oranlarını point-in-time hesaplamak.

Bu sürüm aşağıdaki ailelerin ortak veri omurgasını hazırlar:

- `NONFIN`
- `HOLDING`
- `GYO`
- `INSURANCE`
- `FINANCIAL`

`BANK` ayrı ve daha önce doğrulanmış hattını kullanmaya devam eder.

## Sektör yönlendirme

Yönlendirme tek sözleşmeye taşındı:

```text
XBANK -> BANK
XHOLD -> HOLDING
XGMYO -> GYO
XUMAL -> FINANCIAL
aksi -> NONFIN
```

`XUMAL` geniş bir finans endeksidir; tek başına mevduat bankası anlamına gelmez.
Açık `sector_code` ve ticker override, geniş endeks eşlemesinden önceliklidir.

## Semantik kalemden dönemsel finansala

Yeni hat:

```text
raw KAP fact
-> versioned semantic fact
-> company_metrics_quarterly
-> CORE ratio pipeline
```

Başlıca kurallar:

1. Son dönemler satır sayısına göre değil takvim çeyreklerine göre kurulur.
2. Eksik çeyrekler sıkıştırılmaz.
3. YTD akışlar yalnız tam önceki takvim çeyreği mevcutsa bağımsız çeyreğe çevrilir.
4. `published_at <= analysis_at` point-in-time kapısı ilk adımdır.
5. Gelecekteki restatement geçmiş sonucu etkileyemez.
6. Geçersiz doğrudan pay sayısı, sermaye fallback'i ile gizlenmez.
7. Her türetilmiş satır kaynak bildirim, yayın zamanı ve SHA lineage taşır.
8. Kısmi veri açık `is_complete=false` ve diagnostics ile görünürdür.

## Genel rasyo motoru düzeltmesi

Eski `QuarterSeries`, `lag4q` ve `sum4q` işlemlerinde satır sırasını kullanarak
eksik dönemleri sıkıştırabiliyordu. V13 ile:

- `lag4q` tam dört takvim çeyreği geriye gider,
- `sum4q/ttm` dört ardışık takvim çeyreği ister,
- yinelenen dönem sürümleri deterministik seçilir,
- `days_in_period` eksik çeyrek üzerinden köprü kurmaz.

Yeni `company_ratio_pipeline` yalnız `CORE` oranları hesaplar. Fiyat gerektiren
`VAL` oranları bu hatta sızamaz; onlar sektör değerleme motorları tamamlandığında
ayrı point-in-time fiyat hattından gelecektir.

## Komutlar

Dönemsel şirket finansallarını türetmek:

```bash
python -m src.app.cli materialize-company-facts \
  --derivation-config config/nonbank_fact_derivation.example_v1.json \
  --analysis-at 2026-08-05T20:00:00+03:00 \
  --anchor 2026-06-30
```

Point-in-time CORE oranlarını hesaplamak:

```bash
python -m src.app.cli calc-company-ratios \
  --analysis-at 2026-08-05T20:00:00+03:00 \
  --ratios config/ratios.json
```

Öz denetim:

```bash
make self-audit-nonbank-core
```

## Dış sınır

`config/kap_nonbank_semantic_mapping.example_v1.json` yalnız örnek profildir.
Gerçek MKK ürün payload'ı görülmeden tüm XBRL kalemlerinin resmî ve eksiksiz
eşlemesi olduğu iddia edilmez. Ayrıca bu sürüm sektörlere özel M2 değerleme
motorlarını tamamlamaz; güvenli ortak finansal veri ve CORE rasyo katmanını
hazırlar.
