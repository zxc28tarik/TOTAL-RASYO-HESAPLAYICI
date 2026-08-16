# V15 — HOLDING NAD İskonto Değerlemesi

## Amaç

Holding şirketleri muhasebe özkaynağı otomatik olarak NAD kabul edilmeden,
açık kaynak ve SHA zinciri taşıyan Net Aktif Değer (NAD) kayıtlarıyla değerlenir.
Motor aynı emsal grubundaki diğer holdinglerin piyasa değeri / NAD iskontolarını
leave-one-out biçimde kullanır.

## Akış

```text
Kaynaklı NAD JSONL
→ core.holding_nav_snapshots
→ point-in-time son NAD + fiyat
→ aynı grup / aynı para birimi / aynı pay bazı emsalleri
→ iskonto quantile bandı
→ değerleme güveni
→ HOLDING M2
→ günlük Total Rasyo
```

## Matematik

```text
NAD / pay        = nav_total / shares_out
Piyasa değeri    = current_price × shares_out
Mevcut iskonto   = 1 - piyasa_değeri / nav_total
```

Emsal iskontolarının alt çeyrek, medyan ve üst çeyreği hedef holdingin
NAD/pay değeriyle fiyat bandına çevrilir. Hedef şirket kendi emsal grubuna
katılmaz.

## Fail-closed koşullar

- Açık NAD kaynağı veya SHA yoksa
- NAD yayın zamanı analiz anından sonraysa
- Fiyat veya NAD bayatsa
- Kaynak güveni eşik altındaysa
- Para birimi uyuşmuyorsa
- `share_basis` uyuşmuyorsa
- Emsal sayısı yetersizse
- Band geometrisi veya sayısal alanlar geçersizse

sahte değerleme üretilmez.

## Pay bazı sözleşmesi

`shares_out` ve kullanılan fiyat serisi aynı sermaye artırımı, bölünme ve ters
bölünme bazında olmalıdır. Bu koşul `share_basis` kimliğiyle zorunlu hale
getirilmiştir. Üretim config'i `ADJUSTED_PRICE_SERIES_V1` bekler. Gerçek veri
adaptörü, NAD pay sayısını `core.prices_daily.adj_close` serisinin bazına
getirmeden bu kimliği kullanmamalıdır.

## Yeniden çalışma semantiği

Aynı `analysis_at + valuation_profile + valuation_version` tekrar çalışırsa son
rapor otoritatiftir. Başarı veya ret alan bütün ticker'ların eski HOLDING
valuation/M2 satırları aynı transaction içinde temizlenir ve güncel sonuç yazılır.
Bir sonraki INSERT başarısız olursa transaction geri alınır.

## Komutlar

```bash
make ingest-holding-nav
make run-holding-batch
make self-audit-holding-valuation
```

## Dış sınırlar

- Örnek JSONL gerçek NAD değildir.
- Gerçek holding NAD kaynakları ve pay-bazı dönüştürücüsü henüz bağlanmadı.
- Canlı PostgreSQL 16 migration koşusu bu ortamda yapılamadı.
- Quantile, güven ve yaş eşikleri gerçek BIST dağılımıyla nihai kalibre edilmedi.
