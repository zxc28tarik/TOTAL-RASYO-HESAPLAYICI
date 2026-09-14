# W5 — SMRTG 2023-08 tarihsel M2 canary

Tarih: 2026-09-14. Denetim head'i: `e7c657c023f79b7eaef9219f351fc12ce1f0752b`.
Çalışma dalı: `claude/inspiring-cannon-ecxilb`.
Kanonik pano: [Issue #37](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/37) ·
Sözleşme: [ACTIVE_EXECUTION_LEDGER.md](ACTIVE_EXECUTION_LEDGER.md) §W5.

Ana hattın zorunlu adımı. Defterin W5 kabul ölçütü tek cümledir: *canary ya
gerçek tarihsel M2 + provenance üretir, ya da tam neden ve yeniden açma
koşuluyla `BLOCKED` kalır.* Bu denetim ikinciyi üretmiştir — ve blocker'ın
neden bu hücreye özgü **olmadığını** ölçmüştür.

Denetim yeni bir skor üretmez, hiçbir hücreyi onarmaz, hiçbir eşiği gevşetmez
ve üretim kodunu değiştirmez. Önceki canary receipt'i tekrarlanmamış, her sayı
artifact'lardan yeniden türetilmiş, kohort ve sözleşme kararları
`src/analytics/m2_canary_audit.py` içindeki **üretim yardımcıları çağrılarak**
alınmıştır.

## 1. Sonuç özeti

| Kalem | Sonuç |
|---|---|
| Verdict | **BLOCKED** — tek aktif blocker peer kohortu |
| Aktif blocker | `VERIFIED_SHARE_PEER_COHORT_INSUFFICIENT:safe_peers=0;required=5` |
| W5-A pay/aksiyon kapısı | ✅ **5/5 kontrol geçti** |
| W5-A ek bulgu | Artifact'ın güvensiz pay tabanı **1,98× hatalı**; sertifikasyon onu geçersiz kılıyor |
| W5-B profil uyuşmazlığı | **CONFIG** kökenli ve **çözülmüş** — veri kusuru değil, kod/routing kusuru değil |
| W5-C M2 | **Üretilmedi**; hücre açık ret olarak kalıyor |
| Kohort | 75 NONFIN aday · **0 güvenli peer** · 74 güvensiz · 1 güvenli aday (hedefin kendisi) |
| Sistemik sınır | **60 cutoff'un 0'ında** peer kapısı erişilebilir |
| Hedef test / mutasyon | **58 PASS** / **20/20 KILLED** |

Üretilmeyenler açıkça: M2 yok, Total yok, nötr M2 yok, FOLLOW dönemi yok.

## 2. W5-A — pay ve kurumsal işlem kanıtı yeniden doğrulandı

Sertifikasyon receipt'ine güvenilmedi; pay tabanı ham KAP pay sınıfı
tablosundan **yeniden türetildi** (her sınıfın nominal toplamı, kendi
pay-başına nominaline bölünüp toplanarak).

SMRTG'nin dört tarihli gözlemi var:

| `creationDate` | Türetilen pay | Cutoff öncesi mi |
|---|---:|---|
| 2022-03-25 17:21:27 | 153.000.000 | ✅ |
| 2022-10-21 10:36:57 | 306.000.000 | ✅ |
| **2023-07-31 17:49:42** | **605.880.000** | ✅ **seçilen** |
| 2026-01-30 09:24:46 | 1.817.640.000 | ❌ dışlandı |

Seçilen gözlem cutoff'tan (18:10) **20 dakika önce** yayımlanmış; aynı gün
olduğu için `(2023-07-31, 2023-07-31]` açık-kapalı aksiyon aralığı **boştur** ve
içine hiçbir kurumsal işlem düşemez. Boş olmayan bir aralık için
tamlık iddia edilmemiştir.

Beş kontrolün beşi de geçti:

| Kontrol | Sonuç |
|---|---|
| En son uygun gözlem seçildi | ✅ 605.880.000 kayıtlı değere eşit |
| Kayıtlı taban PIT'tir, "en son bilinen" değildir | ✅ 1.817.640.000 kullanılmadı |
| Aksiyon aralığı boş | ✅ sıfır uzunluklu aralık |
| Pay-başına nominal varsayılmadı | ✅ KAP sınıf tablosundan okundu |
| Boş olmayan aralık için tamlık iddia edilmedi | ✅ |

**Look-ahead duyarlılığı ölçüldü:** 2026 gözlemi kullanılsaydı pay tabanı 3×
şişerdi. Dışlama kozmetik değil, sonucu değiştiren bir karardır.

### 2.1 Artifact'ın güvensiz pay tabanı ve onun geçersiz kılınması

Bu denetimin önceki receipt'te bulunmayan bulgusu: CORE artifact'ın SMRTG için
taşıdığı `shares_out` değeri **306.000.000** ve türetme rotası **güvensiz**
`ISSUED_CAPITAL_OVER_NOMINAL`. Bu, 2023-05-09'da yayımlanan Q1 raporundan
gelen, sermaye artırımı **öncesi** bayat bir sayıdır.

Sertifikasyonun 605.880.000 değeri artifact'ınkinin **1,98 katıdır**. Hangi
tabanın gerçekten kullanıldığı iddia edilmedi, **ölçüldü**: kayıtlı
`market_cap = 12.996.126.462,25`, sertifikalı taban × ham kapanış
(605.880.000 × 21,4500007629) çarpımına eşittir ve artifact tabanına eşit
değildir.

Bu nedenle güvensiz rota **yalnız bu ticker için** ve yalnız tarihli, hash'e
bağlı dış sertifikasyon sayesinde geçersiz kılınmıştır. Denetim bunu bir kapı
olarak uygular: sertifikasyon bulunması yetmez, market cap'in sertifikalı
tabanı izlemesi de gerekir. Başka hiçbir ticker bu bulgudan sertifika almaz.

## 3. W5-B — derivation-profile uyuşmazlığının kökeni

Karar: **CONFIG**, ve **çözülmüş**. Veri kusuru değil; routing/kod kusuru değil.

| Taraf | Profil |
|---|---|
| Artifact | `KAP_BULK_GENERAL_HOLDING_CORE_EXACT_V1@1` |
| Varsayılan config `nonfin_valuation.relative_v1.json` | `KAP_NONBANK_CORE_EXAMPLE@1` |
| Açık sürümlü config `nonfin_valuation.kap_bulk_exact_v1.json` | `KAP_BULK_GENERAL_HOLDING_CORE_EXACT_V1@1` |

Artifact kendi içinde tutarlıdır; uyuşmazlık yalnız **varsayılan** valuation
config'inin başka bir profil adı taşımasından doğar. Ayrı ve açıkça sürümlenmiş
bir config artifact'ın kendi profilini beyan eder, dolayısıyla çözüm
konfigürasyondadır — artifact yeniden adlandırılmamış, kapı zorlanmamıştır
(`forced_profile_rename_allowed = false`). Profil eşleşmesi hem ada hem
**sürüme** bakar; yalnız adı tutan bir config uyuşmazlığı çözmez.

## 4. W5-C — M2 neden üretilmedi

Hedefin kendi kanıtı temizdir. Blocker göreli değerlemenin ikinci ayağıdır:
**peer kohortu**.

Kohort üretim yardımcısı `audit_real_peer_cohort` ile hesaplandı
(eşik gevşetilmedi, `minimum_peer_count = 5`):

| Ölçü | Değer |
|---|---:|
| NONFIN aday (≥4 çeyrek) | 75 |
| Güvenli pay adayı | 1 (yalnız SMRTG, dış sertifikasyonla) |
| **Güvenli peer** | **0** |
| Güvensiz pay adayı | 74 |

Sonuç önceki canary receipt'iyle birebir aynıdır (`prior_receipt_reproduced =
true`); denetim bunu bir kapı olarak uygular ve kohort artık önceki kaydı
üretmezse durur. Yerel tarama kuralı da üretim yardımcısına bağlanmıştır: iki
sayı ayrışırsa `SCAN_AND_PRODUCTION_COHORT_DISAGREE` ile durulur.

### 4.1 74 güvensiz peer neden sertifikalanamıyor

| Neden | Peer |
|---|---:|
| `NON_EMPTY_ACTION_INTERVAL_UNPROVEN` | 59 |
| `NO_USABLE_PRE_CUTOFF_SHARE_CLASS_OBSERVATION` | 15 |

Cutoff öncesi kullanılabilir gözlemi olan 59 peer'de aralık medyanı **810 gün**
(min 4, maks 2.603). Yalnız 4'ü 30 günden, 7'si 90 günden kısadır. Bu 59'un
**28'inde** Yahoo aksiyon envanteri aralığın *içinde* satır taşıyor — yani
oralarda bir kurumsal işlem olduğu pozitif olarak biliniyor, aralık boş değil.

Gözlemi hiç olmayan 15 peer: BERA, BRYAT, ECILC, ENJSA, GENIL, HEKTS, IPEKE,
ISMEN, KOZAA, KOZAL, MGROS, OTKAR, OYAKC, PENTA, SOKM.

### 4.2 Sınırlı açılış hedefi

Kapıyı bu cutoff'ta açacak en küçük kanıt kümesi, en kısa beş penceredir:

| Peer | Pencere başlangıcı | Gün | Yahoo aksiyon satırı (6 yıl) |
|---|---|---:|---:|
| BRSAN | 2023-07-27 18:19:21 | 4 | **0** |
| QUAGR | 2023-07-24 17:07:38 | 7 | **0** |
| TUKAS | 2023-07-24 18:22:30 | 7 | **0** |
| TTRAK | 2023-07-05 17:24:09 | 26 | 6 (hiçbiri pencerede değil) |
| ZOREN | 2023-06-15 16:11:06 | 46 | **0** |

Dördünde envanterin altı yıl boyunca **tek satırı yok**. Altı yıllık BIST100
üyeliği olan bir şirket için sıfır satır, *kanıt yokluğudur, yokluk kanıtı
değildir* — bu envanterle bir pencere boş ilan edilemez. W3'te 162 lineage
hücresi de aynı nedenle `BLOCKED` kalmıştı; eksik olan kanıt aynı kanıttır.

## 5. Blocker bu hücreye özgü değil — sistemik sınır

Denetimin önceki kayıtlarda bulunmayan ölçümü: peer kapısı **hiçbir yerde**
erişilebilir mi? 60 cutoff'un tamamı tarandı.

**Rota A — artifact'ta güvenli pay türetmesi.** 60 cutoff boyunca 4.203 NONFIN
aday hücresi var: **4.089 `ISSUED_CAPITAL_OVER_NOMINAL` + 114 boş + 0
`EXPLICIT_CLASS_NOMINAL_SUM`**. Güvenli rota **hiçbir cutoff'ta tek bir peer
bile** üretmiyor.

**Rota B — sıfır aralıklı dış sertifikasyon.** Bir cutoff'ta kapının açılması
için `5 peer + hedefin kendisi = 6` sertifikalanabilir ticker gerekir.
Dağılım: **56 cutoff'ta 0**, **4 cutoff'ta 1**. En iyi cutoff'ta bile 1.

| Cutoff | Sertifikalanabilir |
|---|---|
| 2021-08-31 | ODAS |
| 2023-05-31 | CEMTS |
| 2023-07-31 | SMRTG |
| 2026-04-30 | ALARK |

**Erişilebilir cutoff sayısı: 0 / 60.** Blocker SMRTG 2023-08'e özgü değil,
yapısaldır. Bu, W6'nın kapsamının kaynak kapsamından tahmin edilemeyeceği
anlamına da gelir — ama bu denetim W6'yı başlatmaz.

## 6. Yeniden açma koşulları

1. **GENEL** — tarihli, kaynağı hash'e bağlı bir kurumsal işlem envanteri;
   boş **olmayan** bir aralığın boş olduğunu kanıtlayabilen türden. Cutoff
   öncesi pay sınıfı gözlemi olan her peer bununla sertifikalanabilir hale
   gelir. W3'ün eksik bulduğu kanıtla aynı kanıttır.
2. **BU CUTOFF İÇİN SINIRLI** — §4.2'deki beş pencerenin her birinde kurumsal
   işlem olmadığının kanıtlanması. Yalnız bunlar 2023-08 kapısını açar.

Bunlardan biri gerçekleşene kadar hücre açık ret olarak kalır; nötr skorla
doldurulmaz, sessizce düşürülmez.

## 7. Değişmeyenler

Ağırlıklar (M2 0,40 / M1 0,18 / M3 0,12 / Ek4 0,16 / Ek1 0,08 / Ek9 0,06), veto
(`good_count < 5`, faktör 0,60), `minimum_peer_count = 5`, coverage eşikleri ve
BIST evreni **değişmedi**. `src/`, `sql/`, `config/` ve mevcut testler hiç
değiştirilmedi. Canlı 131 CORE / 48 M2 / 11 Ek9 / 2 Total / 805 ret ve
RGYAS 46,6021011290 · TABGD 43,9061751217 aynen korunuyor. Tarihsel PIT M2
denetim öncesi 0'dı, sonrası da 0'dır.

## 8. Yeniden üretim

```bash
python scripts/audit_w5_smrtg_canary.py --apply     # artifact + receipt
python scripts/audit_w5_smrtg_canary.py --check     # bayt düzeyinde yeniden üretim
python -m pytest -q tests/test_w5_smrtg_canary.py   # 58 test
python scripts/audit_w5_mutations.py --output data/audit/w5_smrtg_canary_v1/mutations.json
```

Artifact'lar: `data/audit/w5_smrtg_canary_v1/{baseline,cell_verification,
peer_certifiability,systemic_bound,verdict,mutations,receipt}.json`.
Hash modu `LF_CANONICAL_SHA256_V1`; receipt on kaynağı yola + SHA256'ya bağlar.

Mutasyon paketi 20 kapının her birini ayrı ayrı zayıflatır — pay kapısının
sabitlenmesi, cutoff filtresinin kaldırılması, sertifikasyonun yalnız
sertifika varlığından iddia edilmesi, profil sürümünün yok sayılması, kohort
kabul kuralının gevşetilmesi, kapı erişilebilirliğinde bir-eksik hatası,
verdict'in sabitlenmesi dahil. **20/20 KILLED.**
