# Historical PIT Total Rasyo Replay Contract

Status: **PR ADAYI — BAĞIMSIZ DENETİM VE MERGE BEKLİYOR**

Bu belge, 2021-08 ile 2026-07 arasındaki 60 tarihsel cutoff için altı üretim modülünün point-in-time sonuçlarını tek bir Total Rasyo skor/sıralama katmanında birleştirme sözleşmesini tanımlar.

## 1. Formül otoritesi değişmez

Yeni tarihsel katman Total Rasyo formülünü yeniden yazmaz.

Otoritatif üretim zinciri aynen korunur:

- `src/analytics/total_rasyo_score.py::compute_total_rasyo`
- `src/analytics/total_rasyo_combine.py::combine_company_result`

Üretim ağırlıkları:

| Modül | Ağırlık |
|---|---:|
| M2 | 0.40 |
| M1 | 0.18 |
| M3 | 0.12 |
| Ek4 | 0.16 |
| Ek1 | 0.08 |
| Ek9 | 0.06 |

`good_count_ge8 < 5` ise üretim veto sözleşmesi uygulanır; `veto_factor = 0.60` değerini tarihsel katman tanımlamaz veya kopyalamaz, üretim combiner/scorer zincirinden devralır.

`compute_total_rasyo`, mevcut replay fonksiyonları ve onların matematikleri bu PR kapsamında değiştirilmez.

## 2. Tek-cutoff orkestrasyon sırası

`run_historical_pit_total_rasyo_cutoff(...)` bir tarihsel cutoff için mevcut kapalı replay motorlarını çağırır:

1. `run_historical_pit_m1_replay`
2. `run_historical_pit_m3_replay`
3. `run_historical_pit_ek4_replay`
4. `run_historical_pit_ek1_replay` — doğrudan aynı M1 replay sonucunu tüketir
5. `run_historical_pit_ek9_replay`

M3, Ek4 ve Ek9 aynı çağıran tarafından verilen tarihsel `universe`, `trading_calendar`, `stock_prices` ve gereken yerde `index_prices` çerçevelerini kullanır. Orkestratör güncel DB/materialization okumaz.

## 3. M2 özel yolu

M2 için tek bir generic tarihsel replay motoru yoktur ve bu PR yeni bir yedinci M2 motoru oluşturmaz.

Otoritatif mevcut aile replay sonuçları şunlardır:

- BANK
- NONFIN
- HOLDING
- GYO
- INSURANCE
- FINANCIAL

Her aile sonucu yalnız kendi tarihsel ticker kümesini sahiplenir. Üst katman:

- her ticker için tam bir M2 motor sahibi zorunlu tutar;
- aynı ticker iki M2 ailesinde görünürse fail-closed hata verir;
- ailelerin birleşimi tarihsel evreni eksiksiz kapsamazsa fail-closed hata verir;
- M2 score/rejection ayrımını korur;
- `valuation_usable=False` sonucunu skorlanabilir M2 olarak kabul etmez;
- hiçbir mevcut M2 replay fonksiyonunun iç matematiğini yeniden uygulamaz.

M2 aile replay sonuçları üretimdeki `EngineRun` sözleşmesine adapte edilir; diğer beş modül `CompanyModuleContext` sözleşmesine adapte edilir. Son karar `combine_company_result(...)` tarafından verilir.

## 4. Rejection ve eksik veri: fail-closed

Bir ticker için altı modülden herhangi biri eksik/rejected ise:

- Total Rasyo skoru üretilmez;
- eksik modüle nötr skor verilmez;
- kalan ağırlıklar yeniden dağıtılmaz;
- başka cutoff'tan skor ödünç alınmaz;
- güncel veri ile fallback yapılmaz;
- ticker çıktıdan kaybolmaz.

Ticker üst-seviye `rejections` çıktısında görünür ve üretim combiner'ının `total_rasyo_status`, `rejection_reason`, `insufficiency_reason`, `missing_modules` semantiği korunur.

Her cutoff için her ticker tam olarak bir yerde bulunur: ya `scores` ya `rejections`.

## 5. Cutoff ve lineage eşitliği

Birleştirme öncesi aşağıdaki sınırlar birebir eşleşmelidir:

- bütün replay sonuçlarında aynı `analysis_at`;
- M1/M3/Ek4/Ek1/Ek9 için aynı `asof_date`;
- M3/Ek4/Ek9 için aynı `market_asof_date`;
- bütün replay sonuçlarında aynı tarihsel ticker evreni;
- bütün M2 aile sonuçlarında aynı `analysis_at`.

Sadece `<=` kabulüyle modüller arası farklı cutoff'ların birleşmesine izin verilmez; birleşik skor için **tam eşitlik** aranır.

## 6. `good_count_ge8` otoritesi

Tarihsel veto girdisi yeniden hesaplanmaz.

Zincir:

`HistoricalPitRscReplayResult` → `HistoricalPitM1ReplayResult` → `HistoricalPitEk1ReplayResult`

Ek1 zaten M1'in aynı PIT dönem satırını tüketir. Birleştirme katmanı ayrıca:

- M1 ve Ek1 scored ticker kümelerinin aynı olmasını;
- `period_end` değerlerinin aynı olmasını;
- `good_count_ge8` değerlerinin aynı olmasını

zorunlu tutar.

Total Rasyo'ya giden `good_count_ge8`, Ek1 replay çıktısından alınır. Böylece skor ve veto girdisi farklı finansal dönemlere kayamaz.

## 7. Sıralama

Sadece `total_rasyo_status == OK` olan ticker'lar sıralanır.

Sıra:

1. `final_score` azalan
2. eşit skorda `ticker` artan

Bu deterministik tie-break kuralıdır.

## 8. 60-cutoff kilidi

`run_historical_pit_total_rasyo_60_cutoffs(...)` tam 60 aylık diziyi zorunlu tutar:

- ilk ay: `2021-08`
- son ay: `2026-07`
- aylar eksiksiz ve kronolojik sırada olmalıdır.

59/61 cutoff veya yer değiştirmiş ay fail-closed reddedilir.

Bu katman tarihsel skorları/sıralamaları üretir; gerçek portföy çalıştırma ve performans yayını değildir.

## 9. Bu PR'ın bilinçli sınırı

Bu PR şunları **yapmaz**:

- gerçek cutoff/execution saat politikasını seçmez;
- aylık alış/satış portföyünü çalıştırmaz;
- asgari ücret katkısını portföye uygulamaz;
- kurumsal aksiyonlarla NAV simülasyonu yapmaz;
- 5 yıllık getiri/başarı iddiası yayımlamaz;
- `main` dalına dokunmaz.

Gerçek 60 aylık performans ancak birleşik replay bağımsız denetimden geçtikten, gerçek cutoff/execution politikası kilitlendikten ve V24-G readiness sonucu `READY` olduktan sonra yayımlanabilir.

## 10. Denetim/mutasyon kapıları

PR kapanmadan en az şu davranışlar korunmalıdır:

- bir modül rejection'a çevrilince Total Rasyo skorunun kaybolup üst-seviye rejection üretilmesi;
- M2 rejection veya `valuation_usable=False` durumunda skor üretilmemesi;
- modüllerden birinin `asof_date`/`market_asof_date` sınırı kaydırıldığında birleştirmenin reddedilmesi;
- M2 `analysis_at` kaymasının reddedilmesi;
- M1/Ek1 `period_end` veya `good_count_ge8` lineage kaymasının reddedilmesi;
- `good_count_ge8=4` için production veto uygulanması, `5` için uygulanmaması ve gerçek veto faktörünün `0.60` kalması;
- 60 ay dizisinin eksik veya reordered mutasyonunun kırılması;
- M2 tek motor sahipliği ihlalinin reddedilmesi;
- deterministik ticker tie-break sıralaması.

Bağımsız gerçek-diff ve mutasyon denetiminden blocker/major kalmadan geçmeden bu katman **KAPALI** sayılmaz ve merge edilmez.
