# M2 External Data Requirements

## Kesin sonuç

`RAW_CLOSE_BASIS_EVIDENCE_MISSING` sayacı 5.292'den 163'e düştü. Kalan 163 hücrenin tamamında fiyat satırı da yoktur (`PRICE_MISSING`). Dolayısıyla fiyatı bulunan 5.129 hücre için sorun veri yokluğu değil, arşivdeki Yahoo `Close` kolonunun üretim evidence contract'ına bağlanmamış olmasıydı.

Yahoo alımı hash ile sabitlenmiş workflow'da `auto_adjust=False` kullanır; `Close` değerleme fiyatıdır, `Adj Close` yalnız tanı amaçlı saklanır. Ticker, `.IS` sembolü, işlem tarihi, doğrudan çözümleme, discovery/resolved dosya hash'leri ve workflow hash'i mutasyon testleriyle fail-closed doğrulanır. 3.017 adet 5/6-modüllü öncelikli hücrenin tamamı artık raw-close açısından doğrulanmıştır.

Bu tek başına M2 üretmez. Güncel aşama hâlâ:

| Aşama | Raw-close engeli | M2 | Total | >=1 Total ayı | >=6 Total ayı | AL ayı | 3.017'den Total'a dönüşen |
|---|---:|---:|---:|---:|---:|---:|---:|
| v2 başlangıç | 5.292 | 0 | 0 | 0 | 0 | 0 | 0 |
| Yahoo raw Close bağlandı | 163 | 0 | 0 | 0 | 0 | 0 | 0 |

Makine-okunur ölçüm: `data/audit/m2_priority_matrix_v1/m2_stage_measurements.json`.

## 3.017 hücrelik öncelik matrisi

- 3.017 hücre, 143 ticker, 60 ay.
- Aileler: 2.173 NONFIN, 778 HOLDING, 66 GYO.
- 2.854 hücrede tarihli `ISSUED_CAPITAL` gözlemi var; 163 hücrede bu başlangıç durumu da yok.
- 32 hücre composite finansal entity kaynağına bağlı ve ayrıca pay sınıfı uzlaştırması gerektiriyor.
- Ticker/ay listesi: `data/audit/m2_priority_matrix_v1/m2_priority_ticker_months.csv`.
- Cell-level kanıt ve bağımlılıklar: `data/audit/m2_priority_matrix_v1/m2_priority_cells.jsonl.gz`.

## Finansal corpus sonucu

Doğrulanmış üç semantik pakette 199.969 olgu yeniden tarandı. 9.487 adet `ISSUED_CAPITAL` gözlemi bulundu; bağımsız `nominal_value` veya `number_of_shares` olgusu bulunmadı. Bu nedenle sermaye tutarı / varsayılan 1 TL işlemi uygulanmadı:

- `derived_shares = null`
- `share_state_usable = false`
- `action_completeness_provided = false`

Kaynak hash'li 9.487 gözlem `data/audit/m2_share_state_candidates_v1/issued_capital_observations.jsonl.gz` içindedir. Ardışık eşit sermaye gözlemleri yalnız state gözlemidir; aradaki event envanterinin eksiksizliğini kanıtlamaz.

## Resmî Borsa DataStore paketi

Borsa İstanbul resmî sayfası geçmiş verilerin DataStore üzerinden verildiğini belirtir. DataStore format dokümanı haftalık ürünü `serart[YYYY].zip -> sermaye_[YYYY].xls` olarak tanımlar. Dosya yıl içinde gerçekleşmiş/gerçekleşecek sermaye değişikliklerini pay ve şirket bazında içerir; şirket toplamları tüm payları kapsar. Şema önceki/cari sermaye, azaltım, bedelli/bedelsiz artırım, birleşme, ISIN/pay sınıfı ve hak kullanım/pay dağıtım tarihlerini içerir. Portal üyelik ve ücretli satın alma akışı kullanır; yetkisiz erişim denenmemiştir.

Minimum yıllık talep:

| Paket | İç dosya | Bağımlı 3.017 hücre | Ne sağlar |
|---|---|---:|---|
| `serart2021.zip` | `sermaye_2021.xls` | 223 | 2021 state/event aralığı |
| `serart2022.zip` | `sermaye_2022.xls` | 434 | 2022 state/event aralığı |
| `serart2023.zip` | `sermaye_2023.xls` | 796 | 2023 state/event aralığı |
| `serart2024.zip` | `sermaye_2024.xls` | 998 | 2024 state/event aralığı |
| `serart2025.zip` | `sermaye_2025.xls` | 990 | 2025 state/event aralığı |
| `serart2026.zip` | `sermaye_2026.xls` | 443 | 2026 state/event aralığı |

Daha eski paket gerekmiyor: 3.017 hedef hücrede gözlenen en eski share-basis 2021-03-31, ilk fiyat cutoff'u 2021-07'dir.

Satın almadan önce Borsa'ya şu kritik soru sorulmalıdır: altı yıllık teslimat, her backtest cutoff'undan önce yayımlanmış tarihli haftalık snapshot/revizyonları mı içeriyor, yoksa yalnız en son/yıl sonu roll-up mı? Yalnız son roll-up PIT yayın-zamanı kanıtı değildir. Altı yıllık paket tarihli revizyonları içeriyorsa minimum set budur. İçermiyorsa güvenli fallback, `serart202107.zip`–`serart202606.zip` arasındaki tam 60 aylık snapshot'tır. Önce teyit alınmadan 60 dosya satın alınmamalıdır.

Tam ve ticker bazlı procurement manifest:
`data/audit/m2_priority_matrix_v1/m2_procurement_manifest.json`.

Paket alındıktan sonra bile otomatik kabul yoktur. Her ticker için:

1. ISIN/pay sınıfları ve 1 TL nominal birim doğrulanır.
2. Tüm pay sınıfları şirket toplamına uzlaştırılır.
3. Başlangıç state + bütün etkili pay-değiştiren event'ler = bağımsız bitiş state eşitliği aranır.
4. Kaynak yayım zamanı valuation cutoff'unu aşamaz.
5. “Aramada event çıkmadı” sonucu completeness kanıtı sayılmaz.

Resmî fiyat paketi satın almak gerekmiyor; 3.017 hedef hücrenin raw Close gereksinimi mevcut arşivle kapanmıştır.

## BANK: 509 hücre ayrı bağımlılık

Etkilenen dokuz ticker: AKBNK, ALBRK, GARAN, HALKB, ISCTR, SKBNK, TSKB, VAKBN, YKBNK.

- Cutoff-bound ekonomik/model varsayımları: `coe`, `macro_cap`.
- Opsiyonel tarihsel gözlem/tanı: `risk_free_rate` (motor girdisine doğrudan girmez, lineage için saklanır).
- Versioned model/config politikası: `tier_cap=0.80`, `payout_missing_factor=0.70`, `band_width_shadow_mode=true`, `max_halfwidth=0.80`.

Mevcut BANK gate genel olarak aşırı katı değildir: politika sabitlerinin dış tarihsel gözlem gibi satın alınması gerekmez, fakat tarihli `coe` ve `macro_cap` gerçekten yoktur ve 509 hücrenin reddi için tek başına yeterlidir. BANK matematiği değiştirilmemiştir.

## Satın alma sonrası olası kapsama

- En iyi durum üst sınırı: 3.017 yeni Total skoru.
- Satın alma işleminin tek başına garanti ettiği skor: 0.
- Neden: nominal/pay-sınıfı doğrulaması, state/event reconciliation ve gerçek NONFIN/HOLDING/GYO M2 engine materialization hâlâ çalıştırılmalıdır.
- BANK 509 hücresi `serart` ile çözülmez.

Skor üretilmediği için P5 gerçek strateji koşusu ve tam P6 trade/NAV denetimi başlatılmamıştır. Bunun yerine 6.000 hücrelik kaynak→reddetme zinciri bağımsız denetimden geçmiştir; 5.129 Yahoo receipt'i discovery ve resolved satırlarına ikinci kez bağlanmıştır.

## Resmî bağlantılar

- https://www.borsaistanbul.com/veriler/gecmise-donuk-veri-satisi
- https://datastore.borsaistanbul.com/
- https://datastore.borsaistanbul.com/assets/files/DataStore_Veri_Bildirim_ve_Kabul_Formatlar%C4%B1.pdf
- https://datastore.borsaistanbul.com/contact-us
