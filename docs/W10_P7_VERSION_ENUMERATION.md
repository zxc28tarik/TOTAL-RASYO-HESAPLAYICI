# W10 — P7 authoritative historical-version enumeration

Tarih: 2026-09-13. Denetim head'i: `0e0aa0a5074caab1bf978ab4e500059537f89bb2`.
Çalışma dalı: `claude/inspiring-cannon-ecxilb`.
Kanonik pano: [Issue #37](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/37) ·
İlgili: [Issue #36](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/36) ·
Blocker: [Issue #24](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/24) ·
Sözleşme: [ACTIVE_EXECUTION_LEDGER.md](ACTIVE_EXECUTION_LEDGER.md) §W10.

Paralel paket; deneysel W5–W9 hattını bloklamaz. Bu denetim **tamamen
çevrimdışıdır** — repoda zaten bulunan baytlardan türetilmiştir, yeni ağ
isteği yapılmamıştır. Kaynak seçimi değişmedi, hiçbir sonuç `AUTHORITATIVE`
diye etiketlenmedi.

## 1. Sonuç özeti

| Kalem | Sonuç |
|---|---|
| Issue #24 kapanışı | **BLOCKED** — 8 ölçütün **6'sı karşılandı**, 2'si karşılanmadı |
| Karşılanmayanlar | `pit_version_identifiers`, `sector_family_input_coverage` |
| Sürüm zinciri bilinen rapor | **0 / 2.115** |
| Örneklemde supersession oranı | **9 / 423 FR bildirimi = %2,13** |
| Gösterge maruz rapor | **~45** (tek ay örnekleminden; garanti değil) |
| Örneklenen ayda kirlenme | **YOK** — 19 düzeltmenin hiçbiri seçili rapor değil |
| Yollar | 4/4 derecelendirildi; **ücretli kaynak kullanılmadı** |
| Etiket | `EXPERIMENTAL_RISK_ACCEPTED_5Y` korunuyor |
| Hedef test / mutasyon | **25 PASS** / **11/11 KILLED** |

## 2. Issue #24 kapanış ölçütleri — ölçülerek

Önceki kayıt iddiaları tekrarlanmadı; her ölçüt 6.000 hücreye karşı **sayıldı**.

| # | Ölçüt | Durum | Ölçüm |
|---|---|---|---|
| 1 | Gerçek kaynak kimliği ve hash'leri | ✅ | 5.990 seçili rapor, 5.990 `member_sha256`, 2.115 farklı `notification_id`, 22 farklı arşiv |
| 2 | Tam 60 aylık kapsam | ✅ | 60 ay × tam 100 hücre |
| 3 | Aylık tarihsel BIST100 evren bağı | ✅ | 209 farklı ticker, 6.000 hücre |
| 4 | PIT **yayın** zaman damgaları | ✅ | 5.990/5.990 dolu, **0 tanesi cutoff sonrası** |
| 5 | PIT **sürüm** tanımlayıcıları | ❌ | **0 / 5.990**; hepsinde `historical_version_enumeration_complete = false` |
| 6 | Sektör ailesi girdi kapsamı | ❌ | NONFIN 4.054 · HOLDING 833 · BANK 509 · GYO 280 · INSURANCE 82 · FINANCIAL 55 · **aile çözülemeyen 187** |
| 7 | Açık boşluk/ret muhasebesi | ✅ | 6.000/6.000 `EXPLICIT_REJECTION` |
| 8 | Örnek/fixture/current-state fallback yok | ✅ | 0 |

**Kapanışı engelleyen tek asıl kalem 5 numaralı ölçüttür**; 6 numaralı kalem
(187 hücrede aile çözülememesi) ayrı ve daha küçük bir açıktır.

## 3. Enumeration açığı — nicelendi

- Seçili rapor: **5.990**; farklı rapor: **2.115**.
- Sürüm/düzeltme metadata'sı taşıyan rapor: **0**.
- `SUPERSEDED_HISTORICAL_KAP_REPORT_VERSIONS_NOT_ENUMERATED` risk bayrağı
  taşıyan rapor: 5.990 (yani risk zaten her hücrede kayıtlı).

### 3.1 Toplu arşivler bu soruya yapısal olarak cevap veremez

Kurtarılmış gerçek düzeltme çifti (KORTS 2022 yıllık):

| Sürüm | Bildirim | Yayın | Rol |
|---|---|---|---|
| Özgün | 1122417 | 2023-03-09 18:36:13 +03 | `DUZELTILEN` |
| Düzeltme | 1126845 | 2023-03-21 18:32:11 +03 | `DUZENLENEN` |

437 sayısal alanın 4'ü değişmiş: ana ortaklık payları ile kontrol gücü olmayan
paylar birbiriyle yer değiştirmiş (1.833.392 / 2.079.640 ↔ -76 / -155, bin TL).
İki yayın arasındaki bir cutoff'ta düzeltilmiş değeri kullanmak doğrudan bilgi
sızıntısıdır.

Kritik nokta: **korunmuş hash'li `KAP_2022_Y.zip` yalnız yeni bildirimi
içeriyor** (`original_disclosure_in_bulk_archive: false`). Toplu arşivlerden
türetilen bir katalog, daha eski bir sürümün var olduğunu **tespit bile
edemez**. Enumeration'ın bu yolla çözülmesi ilkesel olarak mümkün değildir.

### 3.2 Supersession oranı — tek ay örneklemi

Resmî sorgu baytları elde olan tek pencerede (2023-03-01..2023-03-31):

| Ölçü | Değer |
|---|---:|
| Finansal rapor bildirimi (kategori FR) | 423 |
| `DUZELTILEN` (aşılmış) | 9 |
| `DUZENLENEN` (düzeltme) | 10 |
| Supersession oranı | **%2,13** |

2.115 farklı seçili rapora uygulanırsa **gösterge olarak ~45 rapor** sessizce
aşılmış bir sürüm olabilir. Bu bir büyüklük mertebesi göstergesidir; saklama
garantisi değildir ve 60 ay boyunca sabit varsayılamaz.

## 4. Örneklenen ay için kirlenme sondası

Elimizdeki tek aylık resmî sorgu, doğrudan bir kirlenme testi olarak kullanıldı:

- Düzeltme işaretli FR bildirimi: **19**
- Bunlardan evren ticker'ına dokunan: **1** (BFREN, id 1119330, 2022/Q4)
- Bunlardan herhangi bir hücrede **seçili rapor** olan: **0**

BFREN yalnız 2024-04..2024-09 arasında evrende ve o hücrelerde 2023/Q3, 2023/Q4
ve 2024/Q1 raporları seçilmiş; Mart 2023'te düzeltilen 2022/Q4 raporu hiçbir
hücrede kullanılmıyor.

**Sonuç: `contamination_detected = false`.** Bu 60 ayın 1'i için sınırlı bir
olumsuz sonuçtur; kalan 59 ay için temiz kâğıt değildir.

## 5. Yollar — dördü de derecelendirildi

| Yol | Durum | Maliyet |
|---|---|---|
| Immutable tarihsel KAP bildirim export'ları (toplu arşivler) | `INSUFFICIENT_PROVEN` | ücretsiz, repoda |
| Resmî dağıtım/API enumeration (`disclosure/members/byCriteria`) | `DEMONSTRATED_RETENTION_COMPLETENESS_UNPROVEN` | ücretsiz, pencere başına 1 istek |
| Arşivlenmiş bildirim/sürüm tanımlayıcıları (düzeltme bağlantıları) | `WORKS_PER_DISCLOSURE_NO_GLOBAL_COMPLETENESS` | ücretsiz, bildirim başına 1 istek |
| Yayın/sürüm zaman damgası zinciri | `TIMESTAMPS_COMPLETE_VERSION_CHAIN_ABSENT` | ücretsiz, repoda |

### 5.1 Yeni ve olumlu bulgu — tarihsel saklama gösterildi

Resmî sorgu endpoint'i, **yakalama tarihinden yaklaşık üç buçuk yıl önceki** bir
pencere için 9 adet `DUZELTILEN` satırı döndürdü (HTTP 200, yanıt hash'li).
Yani aşılmış bildirimlerin tarihsel olarak saklandığı **varsayım değil, gösterilmiş
bir olgudur**.

### 5.2 Buna rağmen neden hâlâ BLOCKED

Ücretsiz ve çalıştığı gösterilmiş bir yol var, fakat **çalıştırılmadı** ve
çalıştırılsa bile Issue #24'ün istediği şeyi tam vermez:

- **Verebileceği:** 60 cutoff'u besleyen her yayın penceresi için düzeltme
  işaretleri ve bunların seçili raporlara bağlanması. Bu, açığı ciddi biçimde
  daraltır ve yapılmaya değerdir.
- **Veremeyeceği:** silinmiş veya hiç bağlantı verilmemiş bir sürümün var
  olmadığının kanıtı — Issue #24'ün tamlık ölçütü tam olarak budur.

Ücretli bir kaynak kullanılmadı ve ücretsiz yollar tüketilmeden fiziksel blocker
sayılmadı.

## 6. Karar

- Issue #24 **açık kalır**; W10 `BLOCKED`.
- `historical_version_enumeration_complete = false`,
  `pit_materialization_authorized = false`,
  `real_60_cutoff_scoring_authorized = false` aynen korunur.
- Sonuç **`AUTHORITATIVE_PIT_5Y` diye etiketlenemez**;
  `EXPERIMENTAL_RISK_ACCEPTED_5Y` profili korunur.
- Deneysel W5–W9 hattı bu paket yüzünden bloklanmaz.
- Önerilen sonraki adım (ayrı bir iş olarak): resmî sorgu rotasını 60 cutoff'u
  besleyen bütün yayın pencerelerinde çalıştırıp düzeltme işaretlerini seçili
  raporlara bağlamak. Bu Issue #24'ü kapatmaz ama maruziyeti tahminden ölçüme
  çevirir.

**Yeniden açma koşulu:** tarihsel bir bildirimin bütün sürümlerini saklama veya
tamlık garantisiyle sayan resmî bir kaynak, ya da aşılmış sürümleri koruyan
eşdeğer bir immutable export.

## 7. Yeniden üretim ve kanıt

```bash
python scripts/audit_w10_mutations.py --output data/audit/w10_p7_enumeration_v1/mutations.json
python scripts/audit_w10_p7_enumeration.py --apply
python scripts/audit_w10_p7_enumeration.py --check
python -m pytest -q tests/test_w10_p7_enumeration.py
```

Kanıt dizini: `data/audit/w10_p7_enumeration_v1/` — `issue24_criteria.json`
(8 ölçüt, ölçümleriyle), `enumeration_gap.json`, `sampled_month_probe.json`
(19 düzeltmenin her biri), `routes.json`, `mutations.json`, `receipt.json`.
Hash modu `LF_CANONICAL_SHA256_V1`; `--apply` iki bağımsız türetmenin bayt
düzeyinde aynı olmasını zorunlu tutar. Mutasyon suite'i, ölçülen her sayının
sabitlenmesi hâlinde testlerin bunu yakaladığını kanıtlar.

## 8. Değişmeyen sözleşmeler

Kaynak seçimi, ağırlıklar, veto, eşikler, evren ve nötr-dolgu yasağı değişmedi.
Üretim kodu değişmedi, ağ erişimi yapılmadı. Canlı 48 M2 / 11 Ek9 / 2 Total /
805 ret korunuyor. `main` değişmedi.
