# W6-B — 509 BANK hücresinde tarih-doğru `coe` ve `macro_cap`

Tarih: 2026-09-13. Denetim head'i: `0e0aa0a5074caab1bf978ab4e500059537f89bb2`.
Çalışma dalı: `claude/inspiring-cannon-ecxilb`.
Kanonik pano: [Issue #37](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/37) ·
Sözleşme: [ACTIVE_EXECUTION_LEDGER.md](ACTIVE_EXECUTION_LEDGER.md) §W6-B.

Paralel paket; NONFIN M2 ilerlemesini bloklamaz. Üretim kodu değişmedi, hiçbir
hücre düzeltilmedi, hiçbir current varsayım tarihsele taşınmadı.

## 1. Sonuç özeti

| Parametre | Durum | Kapsam |
|---|---|---:|
| `macro_cap` | **RESOLVED_FROM_OFFICIAL_DATED_SOURCES** | **509 / 509** |
| `coe` | **BLOCKED** (`COE_METHODOLOGY_UNVERSIONED_AND_INPUT_LINEAGE_INCOMPLETE`) | 0 / 509 |
| 509 hücrenin durumu | **BLOCKED** (hepsi) | — |
| Total skor açma üst sınırı | **0** | — |

Bu paket bağımsız bir ikinci geçiştir: önceki araştırma receipt'i yeniden
yazılmadı, **sayılar artifact'lardan yeniden türetilerek** doğrulandı.
Hedef test **22 PASS**, mutasyon **11/11 KILLED**.

## 2. `macro_cap` — bağımsız olarak doğrulandı

Tanım: *en yeni cutoff-uygun OVP'nin son tahmini nominal GSYH / bir önceki
tahmin - 1*. Kaynak: T.C. Strateji ve Bütçe Başkanlığı Orta Vadeli Program
arşivi.

| OVP | Yayın | GSYH son | GSYH önceki | `macro_cap` | Hücre |
|---|---|---:|---:|---:|---:|
| 2021-2023 | 2020-09-29 | 7.021 | 6.310 | 0,112678 | 18 |
| 2022-2024 | 2021-09-05 | 10.287 | 9.041 | 0,137817 | 102 |
| 2023-2025 | 2022-09-04 | 27.440 | 23.438 | 0,170748 | 108 |
| 2024-2026 | 2023-09-06 | 62.997 | 52.942 | 0,189925 | 105 |
| 2025-2027 | 2024-09-05 | 83.132 | 72.915 | 0,140122 | 96 |
| 2026-2028 | 2025-09-07 | 101.397 | 89.406 | 0,134119 | 80 |

Toplam 509 hücre. Beş bağımsız kontrolün beşi de geçti:

| Kontrol | Sonuç |
|---|---|
| Her değer kayıtlı GSYH çiftinden yeniden hesaplandı | ✅ 509/509, sapma yok |
| Hiçbir hücre cutoff'tan **sonra** yayımlanmış OVP kullanmıyor | ✅ 0 ihlal |
| Her hücre **en yeni cutoff-uygun** vintage'ı seçmiş | ✅ 0 ihlal |
| Her hücre P3 artifact'ında gerçekten var | ✅ 509/509 |
| Her hücrenin `historical_family` değeri BANK | ✅ 509/509 |

Her vintage URL + yayın tarihi + boyut + SHA256 ile bağlı.

### 2.1 Kayıtlı sınır — ham PDF baytları repoda değil

`raw_source_bytes_committed: false`. 92 MB'lık git şişmesini önlemek için ham
PDF'ler repoya alınmamış; yalnız URL, yayın tarihi, boyut ve SHA256 kayıtlı.
Sonuç: zincir **yeniden indirmeye karşı denetlenebilir**, fakat çevrimdışı
bayt-düzeyi doğrulama yapılamaz. Bu bir eksiklik olarak açıkça kaydedildi,
gizlenmedi.

## 3. `coe` — BLOCKED, ücretsiz yollar tüketildi

| Yol | Kaynak | Sonuç |
|---|---|---|
| Borsa İstanbul tarihsel endeks verisi | borsaistanbul.com/endeksler/endeks-verileri | Resmî sayfa tarihsel veriyi **ücretli datastore'a** yönlendiriyor |
| TCMB EVDS açık veri platformu | evds3.tcmb.gov.tr | Platform açık, fakat **resmî banka CoE serisi veya sürümlenmiş politika yok** |
| TCMB özkaynak maliyeti çalışma tebliği | tcmb.gov.tr (Deryol) | Yöntem CAPM (10Y TL tahvil risksiz faiz; beta XU100/XBANK ve 2Y TL tahvil), fakat tebliğ **tüm aylık girdilerin Bloomberg terminalinden alındığını** beyan ediyor |

Repoda bulunan tek değer tarihsiz bir demo sabiti (`0,3705`). Tek bir
günümüz sayısını 60 tarihsel cutoff'a uygulamak, defter §2'nin *"current
varsayım tarihsele taşınmaz"* kuralının doğrudan ihlali olurdu; bu nedenle
reddedildi. Geriye doldurma ve ileri bilgi kullanılmadı.

**Yeniden açma koşulu:** ücretli terminal olmadan yeniden oynatılabilen,
tarihli ve sürümlenmiş bir özkaynak maliyeti serisi — ya da tam girdi soy
kütüğü (risksiz faiz eğrisi, hisse risk primi ve banka betası; her biri her
cutoff'ta veya öncesinde yayın zaman damgalı).

## 4. Etki — her iki parametre çözülse bile 0 Total açılır

Bu, paketin en önemli bulgusudur ve sayılar yeniden türetilerek doğrulanmıştır.

| Ölçü | Değer |
|---|---:|
| BANK hücresi | 509 (9 ticker × 60 ay, eksikler hariç) |
| Öncelikli 5/6 modüllü kohort (yeniden hesaplandı) | 3.017 |
| **509 ∩ 3.017** | **0** |
| 509 ∩ (en az 5 modüllü) | 0 |
| 509 ∩ (en az 4 modüllü) | 0 |

BANK hücrelerinde modül durumu:

| Modül | Var | Yok |
|---|---:|---:|
| M3 | 509 | 0 |
| Ek4 | 509 | 0 |
| Ek9 | 483 | 26 |
| **M1** | **0** | **509** |
| **Ek1** | **0** | **509** |
| **M2** | **0** | **509** |

`coe` + `macro_cap` çözülürse M2 hesaplanabilir hale gelir ve en iyi hücre
**3 modülden 4'e** çıkar. Total için **6 modül** gerekir. Dolayısıyla:

> **Bu paketin tek başına açabileceği Total skor sayısı sıfırdır.**

### 4.1 Kalan asıl blocker: CORE tanıları

509 hücrenin tamamında `partial_core_diagnostic_status = NO_CORE_DIAGNOSTICS`:

- 453 hücre: `TECHNICAL_CORE_FAMILY_UNSUPPORTED_OR_CONFLICTING`
- 56 hücre: `OWN_REPORT_STATEMENT_SCOPE_CONFLICT`

M1 ve Ek1 bu katmandan gelir; `coe`/`macro_cap` ile ilgisi yoktur. BANK ailesi
için altı modüllü bir Total, CORE tanı katmanı BANK teknik şemasını
desteklemeden mümkün değildir.

Etkilenen ticker'lar: AKBNK, ALBRK, GARAN, HALKB, ISCTR, SKBNK, TSKB, VAKBN,
YKBNK.

## 5. Karar

- `macro_cap` **çözüldü** ve resmî tarihli kaynaklara bağlandı; ham bayt sınırı
  açıkça kayıtlı.
- `coe` **BLOCKED** kalır; ücretsiz/repo-içi yollar tüketildi.
- 509 hücrenin tamamı **açık ret / BLOCKED** olarak kalır. Kanıtlanamayan hiçbir
  banka hücresi skora dönüştürülmedi.
- Bu paket NONFIN M2 ilerlemesini **bloklamıyor** ve ana hat için bir öncelik
  değişikliği gerektirmiyor: 509 hücrenin hiçbiri 3.017'lik öncelik kohortunda
  değil.
- BANK v4.7 referans motoru davranışı değişmedi; `vendor/v47_roe_belirsizlik`
  içindeki tek `xfail` bilinen matematiksel sınırdır ve dokunulmadı.

## 6. Yeniden üretim ve kanıt

```bash
python scripts/audit_w6b_mutations.py --output data/audit/w6b_bank_coe_macro_cap_v1/mutations.json
python scripts/audit_w6b_bank_coe_macro_cap.py --apply
python scripts/audit_w6b_bank_coe_macro_cap.py --check
python -m pytest -q tests/test_w6b_bank_coe_macro_cap.py
```

Kanıt dizini: `data/audit/w6b_bank_coe_macro_cap_v1/` —
`macro_cap_verification.json`, `coe_blocked.json`, `unlock_bound.json`,
`cells.jsonl` (509 satır: `macro_cap`, OVP soy kütüğü, `coe` durumu, modül
durumu, CORE tanısı), `mutations.json`, `receipt.json`.
Hash modu `LF_CANONICAL_SHA256_V1`; `--apply` iki bağımsız türetmenin bayt
düzeyinde aynı olmasını zorunlu tutar.

## 7. Değişmeyen sözleşmeler

Ağırlıklar, veto, peer/coverage eşikleri, BIST evreni ve nötr-dolgu yasağı
değişmedi. Canlı 48 M2 / 11 Ek9 / 2 Total / 805 ret korunuyor. `main` değişmedi.
