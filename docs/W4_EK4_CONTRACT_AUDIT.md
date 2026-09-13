# W4 — Ek4 fiyat/getiri sözleşmesi ve asimetri denetimi

Tarih: 2026-09-13. Denetim head'i: `0e0aa0a5074caab1bf978ab4e500059537f89bb2`.
Çalışma dalı: `claude/inspiring-cannon-ecxilb`.
Kanonik pano: [Issue #37](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/37) ·
Sözleşme: [ACTIVE_EXECUTION_LEDGER.md](ACTIVE_EXECUTION_LEDGER.md) §W4 ·
Kilitli sözleşme: [HISTORICAL_PIT_EK4_REPLAY_CONTRACT.md](HISTORICAL_PIT_EK4_REPLAY_CONTRACT.md).

Bu paket W7-B final kabul kapısıdır. Üretim kodu **değişmedi**, hiçbir Ek4
değeri yeniden yazılmadı, model/ağırlık/eşik/evren **dokunulmadı**.

## 1. Sonuç özeti

| Kalem | Sonuç |
|---|---|
| Kilitli sözleşme ihlali | **YOK** — verdict `COMPLIANT`, 5/5 kontrol kanıtlı |
| Canlı ↔ tarihsel sapma | **YOK** — iki yol da aynı formülü ve aynı fiyat tabanlarını kullanıyor |
| Ölçülen asimetri etkisi | **310 / 5.820 hücre (%5,33)** maddi; hepsi tek yönlü |
| En büyük skor sapması | **0,3312** (ISMEN 2025-04) |
| Tarihsel XU100 fallback yasağı | **KORUNUYOR** |
| Yeni bulgu | Canlı DB yolundaki XU100 sektör fallback'i provenance'ta **görünmez** |
| Kod değişikliği | **YOK** — metodolojik tercih ayrı yönetişim kararına bırakıldı |
| Hedef test / mutasyon | **23 PASS** / **10/10 KILLED** |

## 2. Sözleşme uyumu — ihlal kanıtlanmadı

Defter §W4: *"Teknik düzeltme yalnız kilitli fiyat/getiri sözleşmesi ihlali
somut olarak kanıtlanırsa yapılır."* Beş kontrolün beşi de geçti:

| Kontrol | Sonuç | Kanıt |
|---|---|---|
| Canlı ve tarihsel tek formülü paylaşıyor | ✅ | iki yol da `src.analytics.ek4_momentum.compute_ek4_momentum_point` çağırıyor |
| Hisse bacağı iki yolda aynı | ✅ | tarihsel `adjusted.where(adjusted.notna(), close)` ↔ canlı SQL `COALESCE(adj_close, close) AS px` |
| Sektör bacağı ham routed endeks kapanışı | ✅ | tarihsel `_finite_positive(out["close"], …)` ↔ canlı `SELECT index_code, trade_date, close AS px` |
| Tarihsel XU100 ikamesi yasak | ✅ | eksik routed uç → `SECTOR_WINDOW_PRICE_MISSING`; XU100 asla ikame edilmiyor |
| Canlı materializer dated route kullanıyor | ✅ | `materialize_current_market_modules.py` M3 paket rotalarını okuyor, belirsizlikte hata veriyor |

Kilitli sözleşme sektör bacağını açıkça **ham routed endeks getirisi** olarak
tanımlıyor ve Ek4 tam olarak bunu yapıyor. Sözleşme hisse bacağının tabanını
(ham mı düzeltilmiş mi) **belirtmiyor**; adapter'daki `COALESCE(adj_close, close)`
tercihi canlı DB yolunda **birebir aynıdır**. Dolayısıyla ne sözleşme ihlali ne
de canlı↔tarihsel sapma vardır.

**Sonuç: kod değişikliği yetkisi yok.** Asimetri metodolojik bir tercihtir ve
ayrı bir yönetişim kararına aittir.

## 3. Ölçülen asimetri — yalnız audit artifact'ında

Hisse bacağı düzeltilmiş (temettü + bedelsiz geri-düzeltmeli), sektör bacağı
ham fiyat endeksi. Bu, pencere içine düşen her dağıtımda hisse getirisini
yukarı, sektör getirisini değiştirmeden bırakır.

Ölçüm yöntemi: sektör bacağı zaten ham olduğu için **sabit tutuldu** ve yalnız
hisse bacağı ham kapanışa yeniden tabanlandı; skor üretim fonksiyonu
`compute_ek4_momentum_point` ile hesaplandı. Kaydedilmiş her getirinin
`adj_close`'tan yeniden üretilebildiği önce fail-closed kapı olarak doğrulandı
(5.820/5.820, maksimum hata 0).

| Bant | Hücre | Anlamı |
|---|---:|---|
| `ADJUSTMENT_IN_WINDOW` | **310** (%5,33) | pencere içinde gerçek geri-düzeltme var |
| `NO_ADJUSTMENT_OR_ROUNDING` | 5.510 | fark yok; kalan sapma `adj_close` yuvarlamasından (maks. 3,8e-07) |
| `ANOMALOUS_NEGATIVE` | **0** | ters yönlü düzeltme yok |

Maddi bantta:

| Ölçü | Değer |
|---|---:|
| Δskor ortalama | 0,0716 |
| Δskor medyan | 0,0517 |
| Δskor maksimum | **0,3312** |
| Δskor > 0,05 | 163 hücre |
| Δskor > 0,10 | 81 hücre |
| Δskor > 0,20 | 18 hücre |
| Etkilenen ay | 49 / 60 |
| Etkilenen ticker | 103 |

Tüm maddi farklar **pozitif**: mevcut taban, ham/ham tabana göre Ek4'ü hiçbir
zaman düşürmüyor, yalnız yükseltiyor. Bu simetrik gürültü değil, tek yönlü bir
sapmadır ve beklenen yöndedir — geri-düzeltme yalnız hisse bacağını kaldırır.

En büyük sekiz sapma:

| Ay | Ticker | Mevcut Ek4 | Ham/ham Ek4 | Δ |
|---|---|---:|---:|---:|
| 2025-04 | ISMEN | 0,8946 | 0,5633 | 0,3312 |
| 2022-04 | EREGL | 0,4410 | 0,1201 | 0,3208 |
| 2022-05 | TTKOM | 0,5438 | 0,2365 | 0,3073 |
| 2025-06 | DOAS | 0,4789 | 0,1796 | 0,2994 |
| 2022-04 | ISDMR | 0,7369 | 0,4378 | 0,2991 |
| 2023-10 | TUPRS | 0,9438 | 0,6623 | 0,2815 |
| 2023-04 | AKBNK | 0,7762 | 0,5275 | 0,2487 |
| 2022-04 | TTRAK | 0,8053 | 0,5580 | 0,2472 |

Ek4 ağırlığı 0,16 olduğundan 0,33'lük bir skor sapması base score'u yaklaşık
0,053 (100'lük ölçekte ~5,3 puan) kaydırır. Bu, tek hücrede AL/İZLE/UZAK
eşiklerini geçirmeye yetebilecek büyüklüktedir.

**Bu karşı-olgusal yalnız artifact'ta yaşar.** Hiçbir Ek4 değeri yeniden
yazılmadı (`ek4_values_rewritten: 0`, `applied_to_production: false`).

### 3.1 Adjusted/adjusted karşılaştırması neden yok

Repoda **toplam getirili sektör endeksi serisi yok**; yalnız fiyat endeksleri
(XU100/XUSIN/XUHIZ/XUTEK) var. Simetrik düzeltme yapılabilmesi için BIST'in
toplam-getiri endeks serisi gerekir; kanıtsız türetme yapılmadı.

### 3.2 Issue #39 ile ilişki

Issue #39 kapsamını açıkça **fiyat-seviyesi valuation / market-cap** ile
sınırlıyor ve *"Adjusted prices may remain correct/intentional for returns,
momentum, beta, alpha and total-return continuity"* diyerek momentumu kapsam
dışı bırakıyor. Dolayısıyla bu asimetri #39'un altına giremez; **kendi
yönetişim kaydını** gerektirir.

## 4. Fallback denetimi

### 4.1 Tarihsel yasak korunuyor

Kilitli sözleşme: boş rota veya XU100'e eşit rota hard input error; eksik routed
uç `SECTOR_WINDOW_PRICE_MISSING` üretir, XU100 asla ikame edilmez. Kod bunu
uyguluyor. Aktif canlı artifact'ları üreten `materialize_current_market_modules.py`
de dated rota okuyor ve belirsizlikte hata veriyor — **fallback uygulamıyor**.

### 4.2 Bulgu — canlı DB yolundaki fallback provenance'ta görünmüyor

`src/analytics/run_daily_pipeline.py::_compute_ek4_momentum` sektör rotasını
`COALESCE(sector_index_code,'XU100') AS sec` ile okuyor. NULL rota sessizce
XU100'e dönüşür, hücre **geçerli bir Ek4 skoru üretir** ve hangi endeksin
kullanıldığı hiçbir yere yazılmaz:

- fonksiyon yalnız `["ticker","ek4"]` döndürüyor;
- `module_rejections` yalnız **skor eksikse** dolduruluyor, fallback'te skor var;
- kalıcılık satırında sektör endeksi kolonu yok;
- `core.universe_stocks.sector_index_code` şemada nullable (`sql/010`).

Aynı desen `betas.py:136` ve `trailing_alpha.py:197` içinde de var.

**Üretim erişilebilirliği:** aktif sonuçların hiçbiri bu yoldan gelmiyor.
`data/live/current_market_modules_v1/modules.csv` içindeki 146 Ek4 değerinin
tamamı dated-route materializer'ından üretilmiştir; DB pipeline'ı aktif artifact
zincirinde kullanılmadı → `NO_PRODUCTION_REACHABILITY_IN_ACTIVE_ARTIFACT_CHAIN`.

Defterin "en dar güvenli düzeltme" kuralı gereği bu, **kanıtı kaydedilerek
bırakıldı, yamalanmadı**.

**Yeniden açma koşulu:** `run_daily_pipeline` tarafından üretilmiş bir Ek4
değeri herhangi bir Total Rasyo iddiasına girmeden önce, çözülen
`sector_index_code` skorla birlikte kalıcılaştırılmalı ya da NULL rota açık
rejection'a dönüşmelidir.

## 5. W7-B için karar

- **Sözleşme kapısı: AÇIK/PASS.** Ek4'ün mevcut fiyat/getiri davranışı kilitli
  sözleşmeyi ihlal etmiyor; W7-B bu gerekçeyle bloklanmaz.
- **Açık yönetişim kalemi:** adjusted-hisse / raw-endeks asimetrisi ölçülmüş,
  tek yönlü ve 49 ayda 310 hücrede maddi. W7-B final kabulünden önce
  kullanıcı/model-yönetişimi kararı olarak görülmelidir. Karar verilene kadar
  mevcut davranış korunur — kanıtsız matematik değişikliği yapılmaz.
- **Açık görünürlük kalemi:** canlı DB yolundaki XU100 fallback'i, o yol bir
  Total Rasyo iddiasına girmeden önce görünür kılınmalıdır.

## 6. Yeniden üretim ve kanıt

```bash
python scripts/audit_w4_mutations.py --output data/audit/w4_ek4_contract_v1/mutations.json
python scripts/audit_w4_ek4_contract.py --apply
python scripts/audit_w4_ek4_contract.py --check
python -m pytest -q tests/test_w4_ek4_contract.py

# W1/W2 kanıt zincirinin bozulmadığı
python scripts/materialize_w2_current_correction.py --check
python scripts/audit_w1_frozen_outputs.py --output w1-after-w4-check.json
```

Kanıt dizini: `data/audit/w4_ek4_contract_v1/` — `contract_compliance.json`,
`price_basis_comparison.json`, `fallback_visibility.json`, `rows.jsonl`
(5.820 hücre, iki taban ve bant), `mutations.json`, `receipt.json`.
Hash modu `LF_CANONICAL_SHA256_V1`; `--apply` iki bağımsız türetmenin bayt
düzeyinde aynı olmasını zorunlu tutar.

## 7. Değişmeyen sözleşmeler

Ağırlıklar (M2 0,40 / M1 0,18 / M3 0,12 / **Ek4 0,16** / Ek1 0,08 / Ek9 0,06),
veto (`good_count < 5`, faktör 0,60), peer/coverage eşikleri, BIST evreni ve
nötr-dolgu yasağı değişmedi. Canlı 48 M2 / 11 Ek9 / 2 Total / 805 ret korunuyor.
`main` değişmedi.
