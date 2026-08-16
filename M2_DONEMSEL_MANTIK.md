# Yeni M1/M2 Mantığı — 8 Dönem ve Dönemsel Band Karşılaştırması

Bu sürümde proje tek günlük M2 mantığından çıkarıldı. Ana karar motoru artık dönemsel çalışır.

## Ana fikir

Sistem şu soruyu cevaplar:

> Son 8 finansal dönemde total rasyo notu yükseliyor mu, beklenen fiyat bandları dönem dönem yukarı gidiyor mu, ama gerçek fiyat bu band yükselişini takip etmeyip geride mi kalıyor?

## M1

M1 artık sadece son RSC değildir. M1, son 8 finansal dönemden çıkarılan total rasyo notu trendidir.

M1 şu alanlardan beslenir:

- son dönem total rasyo notu,
- önceki dönem total rasyo notu,
- son 8 dönem ortalaması,
- 1 dönemlik değişim,
- 4 dönemlik değişim,
- 8 dönemlik eğim,
- güçlü oran sayısı değişimi.

Çıktı tablosu:

```text
analytics.period_8q_comparison
```

## M2

M2 artık sadece “bugünkü fiyat beklenen bandın altında mı?” sorusu değildir.

M2 şu soruları sorar:

- Bu dönem beklenen band ne?
- Önceki dönem beklenen band neydi?
- Bugünkü fiyat eski banda göre nerede?
- Bugünkü fiyat yeni banda göre nerede?
- Band ortası yükseldi mi?
- Fiyat band ortasındaki yükselişi takip etti mi?
- Son 8 dönemde bandlar yükselirken fiyat geride mi kalmış?
- Son 63 günlük trailing alpha bu yorumu destekliyor mu?

Çıktı tablosu:

```text
analytics.m2_period_comparison
```

Örnek M2 yorumu:

```text
Bu dönem beklenen band 28-40.
Önceki dönem band 24-34.
Band ortası 29'dan 34'e çıktı.
Bugünkü fiyat 25.
Fiyat önceki banda göre bandın alt/orta kısmında, yeni banda göre bandın altında.
Son 8 dönemde total rasyo notu yükseliyor.
Beklenen bandlar dönem dönem yukarı gidiyor.
Fiyat bu band yükselişini takip etmiyor, geride kalıyor.
Son 63 günlük alpha hâlâ zayıf ama toparlanma varsa fırsat güçlenir.
```

## Alpha

Ana sistem artık rapor sonrası 63 işlem günü beklemez.

Ana alpha:

```text
analiz günü - 63 işlem günü → analiz günü
```

Yani trailing alpha kullanılır.

Çıktı tablosu:

```text
analytics.alpha_trailing
```

Eski `alpha_realized` ve `decile_map` yapısı geriye dönük kalibrasyon/backtest için korunmuştur. Yeni beklenen band üretici, decile map boşsa muhafazakâr RSC fallback modeliyle yine çalışır.

## Yeni tablolar

Migration:

```text
sql/006_trailing_alpha_period_m2_tables.sql
```

Tablolar:

```text
analytics.alpha_trailing
analytics.period_8q_comparison
analytics.expected_band_periods
analytics.m2_period_comparison
```

## Yeni dosyalar

```text
src/analytics/trailing_alpha.py
src/analytics/period_trend.py
src/analytics/expected_band_periods.py
src/analytics/m2_period.py
```

## Pipeline sırası

```text
ratios_quarterly
→ rsc_summary_quarterly
→ beta_estimates
→ alpha_trailing
→ period_8q_comparison
→ expected_band_periods
→ m2_period_comparison
→ module_scores
```
