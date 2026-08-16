# Geliştirme Raporu — v3 Finansal Mantık Güncellemeleri

Bu sürümde dört geliştirme alanı sırayla uygulandı. Amaç, mevcut dönemsel M1/M2/M3 mantığını korurken finansal karşılaştırmaların doğruluğunu artırmaktır.

## 1) Sektör-nötr skorlama

Önceki durumda tüm hisseler tek havuzda yüzdelik sıralanıyordu ve `sectors.json` içindeki `sector_policies` skorlamada kullanılmıyordu. Bir bankanın kaldıraç oranı sanayi şirketleriyle aynı dağılımda puanlanıyor, bankalar yapısal olarak cezalandırılıyordu.

Yeni durum:

- Yüzdelik sıralama sektör grubu içinde yapılır (BANK / HOLDING / NONFIN). Grup eşlemesi `index_to_group` üzerinden `sector_index_code` ile kurulur; eşleşme yoksa `sector_code`, o da yoksa `*` varsayılanı kullanılır.
- Üye sayısı 5'in altındaki gruplar otomatik olarak tüm evren sıralamasına düşer (küçük grupta yüzdelik anlamsızlaşır).
- `sector_policies.allowed_ratios` artık uygulanır: bir grup için izinli olmayan CORE rasyosu o grubun hisseleri için skorlanmaz (örn. bankalar için CURRENT_RATIO).
- `pillar_weights_override` ve `ratio_weights_override` CORE toplulaştırmasında ağırlıklı ortalama olarak uygulanır.
- Winsorization ve yön dönüşümü her sıralama havuzunun kendi içinde yapılır.

İlgili kod: `src/analytics/rsc_scoring.py` (`score_quarter`, `load_sector_config`, `build_sector_group_map`), `src/analytics/run_daily_pipeline.py` (1. adım).

Geriye uyumluluk: `score_quarter` sektör parametreleri verilmeden çağrılırsa eski havuz davranışı korunur.

## 2) Değerleme ekseni (rsc_val_norm)

Önceki durumda `rsc_val_norm` her zaman NULL yazılıyordu; sistem "kalite yükseliyor, fiyat geride" diyor ama "zaten pahalı mıydı?" sorusunu sormuyordu.

Yeni durum:

- `rsc_val_norm`, LOWER_BETTER tipli VAL rasyolarının (PE_TTM, PB, PS_TTM, EV_EBIT_TTM) yüzdelik skorlarının ortalamasından hesaplanır. Yüksek değer = evrene göre ucuz. Boyut vekilleri (MARKET_CAP_PROXY, EV_PROXY) bilinçli olarak ucuzluk hesabının dışında tutulur.
- VAL rasyoları sektör politikalarından etkilenmez; her hisse için her zaman skorlanır (sıralama yine sektör-nötrdür).
- M2 final skoruna `m2_valuation_support_score` bileşeni eklendi. Yeni bileşim:

```text
m2_final = 0.30*band + 0.22*takip + 0.18*süreklilik + 0.10*alpha + 0.10*kalite + 0.10*değerleme
```

- M2 yorum metnine değerleme cümlesi eklendi ("Değerleme çarpanları evrene göre ucuz/pahalı tarafta...").
- Yeni kolon: `analytics.m2_period_comparison.m2_valuation_support_score` (migration: `sql/007_valuation_and_backtest_ext.sql`).

## 3) Band kalibrasyonu + volatilite

Önceki durumda fallback band yarı-genişliği tüm hisseler için sabit %12,5 idi. BIST'te 63 günlük volatilite hisseye göre 2-3 kat değiştiği için sabit sigma, sakin hisselerde geniş, oynak hisselerde dar band üretiyordu.

Yeni durum:

- Her band için hissenin t0 tarihine kadarki son 63 günlük gerçekleşmiş günlük volatilitesi ölçülür ve `sqrt(horizon)` ile ufka ölçeklenir: `sigma_h = std_gunluk * sqrt(63)`.
- Sonuç [0.08, 0.45] aralığına kırpılır (likit olmayan isim bandı patlatamaz, çok sakin isim bandı çökertemez).
- Decile map'ten gelen sigma da aynı aralığa kırpılır; decile sigma boş/sıfır gelirse hissenin kendi volatilitesi kullanılır.
- Fallback orta nokta eğimi `FALLBACK_ALPHA_SLOPE = 0.40` sabitine taşındı (RSC 1.0 → +%20), gerektiğinde tek yerden ayarlanabilir.

### Beta modeli

İki faktörlü OLS'de XU100 ile sektör endeksi yüksek korelasyonluydu; beta ayrışması kararsızdı. Yeni model:

```text
getiri = a + b1*piyasa + b2*(sektör - piyasa)
```

- İkinci faktör sektör FAZLASI olduğu için çoklu doğrusallık büyük ölçüde kalkar.
- Vasicek tarzı shrinkage eklendi: `w = n/(n+126)` ağırlığıyla b1 → 1.0'a, b2 → 0.0'a çekilir; kısa örneklemlerde tahmin stabilize olur.
- Tutarlılık: `trailing_alpha.py` ve `alpha_realized.py` aynı ayrıştırmayı kullanır: `alpha = getiri - b1*piyasa - b2*(sektör - piyasa)`.

Önemli: Eski modelle üretilmiş beta kayıtları yeni ayrıştırmayla uyumsuzdur. `run-daily` her koşuda betaları yeniden ürettiği için canlı akışta sorun yoktur; ancak geçmiş `alpha_realized` kayıtlarını yeni modelle yeniden üretmek isterseniz ilgili tarih aralığı için pipeline'ı tekrar çalıştırın.

## 4) Backtest genişletmesi + ağırlık optimizasyonu

### Backtest metrik hataları (gerçek koşuda ortaya çıktı)

Sentetik veriyle uçtan uca koşulduğunda üç ciddi hata görüldü ve düzeltildi:

1. **Boş dönemler nakit sayılıyordu.** AL sinyali olmayan dönemlerde `port_ret = 0.0` yazılıp bileşiliyordu. 36 dönemin 27'sinde AL yoktu; sonuç monoton bir sermaye eğrisi, `max_drawdown = 0` ve anlamsız Sharpe oldu. Artık `n_al = 0` olan dönemler risk metriklerinden ve bileşikten dışlanır; `coverage` alanı dönemlerin yüzde kaçında gerçekten yatırım yapıldığını raporlar.
2. **Yıllıklandırma rebalance sayısını gün sanıyordu.** `ann_factor = 252/n` formülü 36 rebalance'ı 36 işlem günü kabul edip Sharpe'ı 291 gösteriyordu. Artık yıllıklandırma koşunun gerçek takvim aralığı üzerinden yapılır (boşta geçen süre de gerçek süredir); volatilite ise gerçek rebalance adımıyla (`step5` → 5 işlem günü) ölçeklenir.
3. **Küçük örneklemde sahte risk metrikleri.** 20'den az yatırımlı dönem varsa Sharpe ve bilgi oranı artık `NULL` bırakılır ve uyarı basılır.

Ek olarak `hold > rebalance adımı` durumunda pencerelerin örtüştüğü ve bileşik eğrinin iyimser okunması gerektiği uyarısı verilir.

### Backtest metrikleri

- `turnover_avg`: ardışık AL listeleri arasındaki değişim oranı (1 - kesişim/büyük liste). Yüksek turnover, işlem maliyeti duyarlılığı demektir.
- `info_ratio`: rebalance başına (portföy - benchmark) fazla getirisinin yıllıklandırılmış bilgi oranı. step < hold olduğunda pencereler örtüşür; bu metrik mutlak değil karşılaştırmalı okunmalıdır.
- Yeni kolonlar `analytics.backtest_runs` tablosuna migration 007 ile eklenir.

### optimize-weights komutu

```bash
python -m src.app.cli optimize-weights --start 2024-01-01 --end 2026-01-01 --hold 20 --step 0.10 --objective ic
```

- `analytics.module_scores` geçmişini okur, her asof tarihi için ileri `--hold` işlem günlük XU100-fazlası getiriyi hesaplar.
- 6 modül (M2, M1, M3, Ek4, Ek1, Ek9) üzerinde `--step` adımlı tam simpleks taraması yapar (varsayılan 0.10 → 1287 kombinasyon; `--min-m2` ile M2 tabanı korunur, varsayılan 0.15).
- Amaç fonksiyonları: `ic` (bileşik skor ile ileri fazla getiri arasındaki ortalama Spearman rank-IC) veya `topq` (bileşik skora göre ilk %20'nin ortalama fazla getirisi).
- Çıktılar: tüm kombinasyonların sonucu `outputs/weight_opt_<id>.csv`, en iyi ağırlıklar `--weights` ile doğrudan kullanılabilir formatta `outputs/weights_optimized_<id>.json`. JSON meta bölümünde modül bazlı tekil IC'ler de raporlanır.

Gerçek koşu örneği (sentetik veri, 36 tarih): en iyi kombinasyon Ek1 ağırlıklı çıktı ama amaç değeri yalnızca 0,057 — yani gürültü seviyesinde. Modül bazlı IC'lerin hepsi ±0,06 bandındaydı. Bu, aracın doğru davrandığını gösterir: veri sinyal içermediğinde optimizasyon sinyal uydurmaz. Ağırlıkları değiştirmeden önce modül IC'lerinin anlamlı büyüklükte (kabaca |IC| > 0,03 ve tutarlı işaretli) olduğunu görmek gerekir.

Aşırı uyum uyarısı: Az sayıda rebalance tarihiyle bulunan "en iyi" kombinasyon gürültüdür. Önce modül bazlı IC'lere bakın; geniş tarih aralığı ve `step5` üzeri örneklem tercih edin. En sağlıklısı, ağırlıkları bir dönemde optimize edip başka bir dönemde backtest ile doğrulamaktır (walk-forward).

## Bilinen sınırlar ve sonraki adaylar

- Enflasyon/TMS 29 etkisi hâlâ ele alınmadı: bandlar ve fiyat değişimleri nominal TL'dir. XU100-relatif band opsiyonu doğal sonraki adımdır.
- Hacim verisi ingest edilmiyor; likidite filtresi/vetosu kurulamıyor (`prices_daily.volume` kolonu hazır, yfinance zaten volume döndürüyor — bağlantı küçük iş).
- `ek3` ve `ek5_dilution` kolonları hâlâ boş: bedelli/sulandırma takibi BIST için değerli bir sonraki modül.
- `trend_bonus` alanı hâlâ pasif (0.0 yazılır); ya aktive edilmeli ya kaldırılmalı.
- AL eşiği (final_score >= 0.70) pratikte çok katı: sentetik koşuda dönemlerin yalnızca %25'inde sinyal üretti. Eşik ya düşürülmeli ya da sinyal yoğunluğu bir kalibrasyon parametresi haline getirilmeli.
- Enflasyon düzeltmesi yapılmadığı için bandlar nominal TL'dir; sentetik veride bile band ortalarının dönem dönem yükselmesinin bir kısmı nominal büyümedir.

## Doğrulama

Bu sürüm gerçek PostgreSQL 16 üzerinde uçtan uca koşuldu:

- 24 hisse (6 banka, 4 holding, 14 sanayi), 778 işlem günü, 10 çeyrek finansal içeren sentetik BIST verisi üretildi.
- `migrate` → `ingest-*` → `calc-ratios` → `run-daily` → `backtest` → `optimize-weights` zinciri hatasız tamamlandı.
- Üretilen kayıtlar: 240 RSC özeti (240'ında `rsc_val_norm` dolu), 24 beta, 24 trailing alpha, 192 dönemsel band, 24 M2 karşılaştırması, 24 modül skoru.
- Değerleme skorları 0,07–1,00 aralığında gerçek dağılım gösterdi (fallback değil).

### Koşu sırasında bulunan ve düzeltilen ek hata

`ratios_calc.fetch_prices_for_pairs` fiyatı `trade_date = t0_date` ile tam eşleştiriyordu. `t0_date` hafta sonuna veya tatile denk geldiğinde (örneğin cuma akşamı KAP bildirimi → t0 cumartesi) fiyat bulunamıyor, tüm VAL rasyoları `is_na` oluyor ve değerleme ekseni sessizce boş kalıyordu. Sentetik veride son iki çeyrekte tam olarak bu yaşandı. Fiyat eşleşmesi artık `t0_date`'ten geriye doğru en son mevcut fiyatı alır (10 günlük geriye bakma sınırıyla; uzun süreli işlem durması hâlinde bayat fiyat sızmasın diye). Bu, `expected_band_periods` içindeki `p0` seçimiyle de aynı konvansiyondur.
