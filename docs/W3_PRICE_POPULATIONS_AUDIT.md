# W3 — Üç fiyat popülasyonu, kesişim yapısı ve ticker lineage

Tarih: 2026-09-13. Denetim head'i: `0e0aa0a5074caab1bf978ab4e500059537f89bb2`.
Çalışma dalı: `claude/inspiring-cannon-ecxilb` (`codex/astra-v24-finalize` üzerinden).
Kanonik pano: [Issue #37](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/37) ·
Sözleşme: [ACTIVE_EXECUTION_LEDGER.md](ACTIVE_EXECUTION_LEDGER.md) §W3.

Bu iş paraleldir: ana M2 hattını (W5→W9) bloklamaz ve onu başlatmaz.
Hiçbir hücre düzeltilmedi, hiçbir skor üretilmedi, hiçbir eşik gevşetilmedi.
Üretim kodu değişmedi.

## 1. Sonuç özeti

| Kalem | Sonuç |
|---|---|
| Üç popülasyon ayrı anahtarla yeniden üretildi | **EVET** — 163 / 174 / 402 birebir doğrulandı |
| Kesişim/fark raporu | **YAYIMLANDI** — birleşim 402, naif toplam 739, **337 mükerrer** |
| 174 execution hücresi reason-code exhaustiveness | **174/174**, `UNRESOLVED_BLOCKED` = 0 |
| Ticker lineage kabul edilebilirliği | **0/5 alias kabul edilebilir** → 162 hücre `BLOCKED` |
| 12 P2 hücresi | `SOURCE_SYMBOL_GAP` — mevcut açık ret korunuyor |
| Mutasyon | **11/11 KILLED** |
| Hedef test | **37 PASS** |
| Canlı sonuçlar | 131 CORE / 48 M2 / 11 Ek9 / 2 Total / 805 ret — **değişmedi** |

## 2. Popülasyonlar ayrı anahtarlarla

Defter §W3 bu üç sayının kesişim raporu olmadan birbirinin yerine
kullanılmasını veya toplanmasını yasaklıyor. Her biri kendi üreticisinden,
kendi anahtarıyla yeniden üretildi:

| Popülasyon | Sayı | Anahtar | Üretici |
|---|---:|---|---|
| `PRICE_MISSING` | 163 | ticker + cutoff/month | `scripts/materialize_experimental_p3_p4.py` |
| `EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING` | 174 | ticker + signal/execution date | `scripts/audit_experimental_readiness.py::run_audit` |
| `STOCK_WINDOW_PRICE_MISSING` | 402 | ticker + analysis window | `src/analytics/historical_pit_{ek9,ek4,m3}_replay.py` |

### 2.1 Bulgu — "402" tek bir popülasyon değil

Düz P4 `reasons` kümesi üç farklı analiz penceresini tek etikete indiriyor.
Pencere sahibi yalnız P3 `module_reasons` haritasında görünür:

| Modül | Hücre | Ek9'un alt kümesi mi |
|---|---:|---|
| Ek9 | 402 | — |
| M3 | 189 | evet |
| Ek4 | 180 | evet |

Yani defterdeki 402, **Ek9 lookback penceresi** popülasyonudur; M3 ve Ek4
pencereleri onun içinde yer alır. Birleşim düz P4 kümesiyle birebir eşleşiyor
(auditor bu eşitliği fail-closed kapı olarak zorunlu tutuyor).

## 3. Kesişim yapısı

```
|PRICE_MISSING| = 163      |EXECUTION| = 174      |STOCK_WINDOW| = 402
birleşim = 402             naif toplam = 739      mükerrer sayım = 337
```

| İlişki | Sonuç |
|---|---|
| `EXECUTION ⊆ STOCK_WINDOW` | **evet** (`EXECUTION \ STOCK_WINDOW` = ∅) |
| `PRICE_MISSING ⊆ STOCK_WINDOW` | **evet** |
| `PRICE_MISSING ⊆ EXECUTION` | **hayır** — tek istisna aşağıda |
| `birleşim = STOCK_WINDOW` | **evet** |

**Üç sayının toplanması 337 hücreyi mükerrer sayar.** Üçü de aynı 402 hücrelik
kümenin farklı kesitleridir; ayrı ayrı raporlanmaları gereken şey hangi kapıyı
bloke ettikleridir, büyüklüklerinin toplamı değildir.

### 3.1 `PRICE_MISSING \ EXECUTION` = tek hücre: `EFOR 2025-11-03`

Bu hücre lineage sınırının imzasıdır. `EFORC → EFOR` değişikliği 2025-11-03'te
etkin; 2025-11-03 sinyal gününde **yeni** kod altında fiyat satırı var (bu yüzden
execution popülasyonunda değil), fakat cutoff `2025-10-31T18:10+03:00` olduğu
için sınırlı (bounded) değerleme penceresi tamamen **eski** kod dönemine düşüyor
ve orada `EFOR` altında satır yok. Sonraki iki ay (`2025-12`, `2026-01`)
`PRICE_MISSING` taşımıyor.

Bu, iki popülasyonun neden ayrı anahtar taşıması gerektiğinin doğrudan kanıtı:
sinyal-günü kapsaması, cutoff-öncesi pencere kapsamasını **kanıtlamaz**.

## 4. 174 execution hücresi — reason-code exhaustiveness

Defterin kapalı taksonomisi kullanıldı; her hücreye **tek** neden atandı.

| Reason code | Hücre |
|---|---:|
| `TICKER_LINEAGE` | **162** |
| `SOURCE_SYMBOL_GAP` | **12** |
| `NO_TRADING_SESSION_OR_NO_TRADE` | 0 |
| `CALENDAR_OR_SESSION_GAP` | 0 |
| `INGESTION_OR_PARSER_GAP` | 0 |
| `OTHER_EVIDENCED` | 0 |
| `UNRESOLVED_BLOCKED` | **0** |

Ticker bazında:

| Ticker | Hücre | Neden |
|---|---:|---|
| KOZAA | 52 | `TICKER_LINEAGE` |
| KOZAL | 52 | `TICKER_LINEAGE` |
| IPEKE | 37 | `TICKER_LINEAGE` |
| KERVT | 14 | `TICKER_LINEAGE` |
| EFORC | 7 | `TICKER_LINEAGE` |
| KLRHO | 6 | `SOURCE_SYMBOL_GAP` |
| INVES | 3 | `SOURCE_SYMBOL_GAP` |
| ASGYO | 3 | `SOURCE_SYMBOL_GAP` |

### 4.1 Bulgu — 162 hücrede fiyat verisi eksik değil

`scripts/audit_experimental_readiness.py`, execution fiyatını
`prices.ticker.eq(prices.price_source_ticker)` ile **exact-ticker** kapısından
geçiriyor. 174 hücrenin **162'sinde** tam `(ticker, signal_date)` çiftinde bir
fiyat satırı **vardır**; satır yalnızca `price_resolution =
BORSA_LINEAGE_YAHOO_ALIAS` olduğu için kapıdan geçmez.

Dolayısıyla bu 162 hücre bir **veri yokluğu** değil, bir **lineage
kabul-edilebilirliği** sorusudur. Kapı bilinçli fail-closed'dır ve bu denetimde
değiştirilmemiştir.

### 4.2 12 hücre — gerçek kaynak kapsama boşluğu

Bu 12 hücrede ilgili `(ticker, signal_date)` için **hiç** satır yok ve çözülmüş
kaynak serisi o tarihi kapsamıyor:

| Ticker | Eksik sinyaller | Kaynak serisi ilk günü |
|---|---|---|
| INVES | 2022-07-01, 2022-08-01, 2022-09-01 | 2024-03-11 |
| KLRHO | 2023-01-02 … 2023-06-01 (6 ay) | 2024-03-21 |
| ASGYO | 2024-01-02, 2024-02-01, 2024-03-01 | 2024-03-21 |

Bu, Issue #31'in bilinen 12 hücrelik pre-cutoff fiyat boşluğudur. Mevcut açık
ret korunur; signal-day/open veya cutoff-sonrası ikame yapılmadı.

## 5. Ticker lineage kabul edilebilirliği

Defter §W3-C: eşleme yalnız **aynı şirket/pay sınıfı kimliği**, **etkinlik
tarihi**, **kurumsal işlem sürekliliği** ve **kaynak sembolü** doğrulanırsa
yapılabilir. Ticker değişikliği tek başına fiyat taşıma izni değildir.

| Alias | Etkinlik | Hücre | Kimlik | Etkinlik tarihi | CA sürekliliği | Kaynak sembol | Kabul |
|---|---|---:|---|---|---|---|---|
| KOZAA→TRMET | 2025-11-24 | 52 | ✅ | ✅ | ❌ | ✅ | **HAYIR** |
| KOZAL→TRALT | 2025-11-24 | 52 | ✅ | ✅ | ❌ | ✅ | **HAYIR** |
| IPEKE→TRENJ | 2025-11-24 | 37 | ✅ | ✅ | ❌ | ✅ | **HAYIR** |
| KERVT→BESLR | 2025-06-02 | 14 | ❌ | ✅ | ❌ | ✅ | **HAYIR** |
| EFORC→EFOR | 2025-11-03 | 7 | ✅ | ✅ | ❌ | ✅ | **HAYIR** |

- **Etkinlik tarihi ve kaynak sembol (5/5 kanıtlı):** resmî Borsa İstanbul
  ticker-değişim workbook satırı, `source_workbook_sha256` +`event_sha256` ile;
  `src/analytics/historical_price_aliases.py` fail-closed resolver'ı satırı
  yalnız etkinlik tarihinden **kesin önceki** günler için etiketliyor.
- **Kimlik (4/5 kanıtlı):** `kap_share_class_history_v1` gözlemleri ardıl kod
  altında mevcut (TRMET 2, TRALT 3, TRENJ 2, EFOR 2). **BESLR için 0 gözlem** →
  KERVT kimliği kanıtlanamadı.
- **Kurumsal işlem sürekliliği (0/5 kanıtlı):** `historical_member_actions_yahoo`
  envanterinde eski kodların tamamı 0 satır; TRALT'ın iki temettüsü (2022-07-07,
  2023-07-14) yalnız ardıl kod altında duruyor. Beş yıllık BIST100 üyeliği için
  bu envanter **eksik**; boş envanter "olay olmadı" kanıtı sayılmaz.

**Sonuç:** 5 alias'ın hiçbiri exact historical execution price kaynağı olarak
kabul edilebilir değil. 162 hücre `BLOCKED` kalır.

### 5.1 KERVT ayrı tutuldu

KERVT→BESLR **2025-06-02** tarihli ayrı bir etkinliktir ve Koza kümesinin
(2025-11-24) parçası değildir; EFORC→EFOR de **2025-11-03** ile kendi
etkinliğidir. Defterin "KERVT Koza lineage'ına dahil edilmez" kuralı test ile
kilitlendi. EFOR/EFORC sabit istisna varsayımıyla değil, hücre bazında ele alındı.

## 6. Yeniden açma koşulları

| Kalem | Hücre | `BLOCKED` gerekçesi | Yeniden açma koşulu |
|---|---:|---|---|
| 5 alias | 162 | `ACTION_CONTINUITY_UNPROVEN` | Lineage sınırı boyunca sürekli, tarihli ve kaynak-hash'li kurumsal işlem envanteri |
| KERVT ek olarak | 14 | BESLR için pay sınıfı gözlemi yok | BESLR altında KAP pay sınıfı/`mkk_member_oid` süreklilik kanıtı |
| P2 boşluğu | 12 | `SOURCE_SYMBOL_GAP` | Cutoff öncesi resmî Borsa/THB fiyatı (Issue #31) |

Tüketilen ücretsiz/repo-içi yollar: çözülmüş Yahoo fiyat seti, resmî Borsa
ticker-değişim workbook'u, Yahoo kurumsal işlem envanteri, KAP pay sınıfı
geçmişi, XU100 sinyal takvimi. Ücretli kaynak kullanılmadı, yeni veri çekilmedi.

## 7. Kapıların üstündeki etki

- **W8 (seçili işlem fiyatı):** deneysel P5 tüm 174'ü gerektirmez; fakat
  seçilen her alış/satış için exact fiyat şarttır. Bu 162 hücre, CA sürekliliği
  kanıtlanmadan exact execution price sağlayamaz → o ay `EXECUTION_BLOCKED`
  olur, sessiz ikame yapılmaz.
- **W9 (tam V24-G):** tam kapsam 174/174 çözüm ister; bugün 0/174 çözülmüştür,
  fakat 174/174'ü **nedeni kanıtlı**dır.
- **Ana M2 hattı:** 163 hücre topluca öne alınmadı. Hepsi 402'nin içinde ve
  6 ticker'a (EFOR, EFORC, IPEKE, KERVT, KOZAA, KOZAL) sınırlı; W6 sırasında
  gerçek Total'e dönüşebilecek bir hücreyi bloke ederse hücre bazında ele alınır.
- **Issue #39** (fiyat-seviyesi valuation / `COALESCE(adj_close, close)`) bu
  denetimde **değiştirilmedi**; W3 yalnız etkilenen hücreleri işaretler.

## 8. Yeniden üretim ve kanıt

```bash
python scripts/audit_w3_mutations.py --output data/audit/w3_price_populations_v1/mutations.json
python scripts/audit_w3_price_populations.py --apply
python scripts/audit_w3_price_populations.py --check
python -m pytest -q tests/test_w3_price_populations.py

# W1/W2 kanıt zincirinin bozulmadığı
python scripts/materialize_w2_current_correction.py --check
python scripts/audit_w1_frozen_outputs.py --output w1-after-w3-check.json
```

`--apply` iki bağımsız türetmenin bayt düzeyinde aynı olmasını zorunlu tutar;
`--check` hem saklanan hem yeniden türetilen her artifact'ı receipt hash'ine
karşı doğrular ve her kaynağı hash'e bağlar (`LF_CANONICAL_SHA256_V1`).

Kanıt dizini: `data/audit/w3_price_populations_v1/`
— `populations.json`, `intersections.json`, `execution_reason_codes.json`
(174 hücrenin tamamı kanıtıyla), `lineage_admissibility.json`, `rows.jsonl`
(402 satır, üçlü üyelik bayrağı), `mutations.json`, `receipt.json`.

Mutasyon suite'i 11 kapıyı ayrı ayrı zayıflatır ve hepsinin KILLED olmasını
şart koşar; üretim checkout'u ve veritabanı hiç kullanılmaz.

## 9. Değişmeyen sözleşmeler

Ağırlıklar (M2 0.40 / M1 0.18 / M3 0.12 / Ek4 0.16 / Ek1 0.08 / Ek9 0.06),
veto (`good_count < 5`, faktör 0.60), minimum peer sayısı ve valuation coverage
eşikleri, BIST evreni ve "eksik veri nötr skora doldurulmaz" kuralı
değiştirilmedi. Canlı 48 M2 / 48 FOLLOW / 11 Ek9 / 2 Total / 805 ret / 807 evren
ve RGYAS 46.6021011290 · TABGD 43.9061751217 değerleri aynen korunuyor
(`materialize_w2_current_correction.py --check` ve `audit_w1_frozen_outputs.py`
ile doğrulandı). `main` değişmedi.
