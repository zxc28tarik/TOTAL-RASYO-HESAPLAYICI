# Total Rasyo — Aktif Kalan İşler ve Yürütme Defteri

Son güncelleme: 2026-09-13
Kanonik GitHub panosu: [Issue #37](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/37)  
Aktif entegrasyon PR'ı: [PR #40](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/pull/40)

Bu belge, sohbetler ve uygulayıcılar değişse bile kalan işlerin sırasını,
kanıtlarını ve kabul durumunu koruyan sürümlenebilir yürütme defteridir. GitHub
Issue #37 kısa ve canlı pano; bu dosya ayrıntılı sözleşmedir. Çelişki halinde
en yeni doğrulanmış commit/CI kanıtı esas alınır ve iki kayıt aynı iş adımında
birlikte güncellenir.

## 1. Doğrulanmış başlangıç durumu

- Aktif dal: `codex/astra-v24-finalize`
- Doğrulanmış başlangıç head'i: `e55d1df38cabd886ff0fbfef758a38eabf160a12`
- `e55d1df`, yanlışlıkla eklenen üç Ek9 devam commit'ini geri alır ve dosya
  ağacını `fad20cccc88f230628999c1a06770c0f7329a12c` ile birebir eşitler.
- PR #40: draft, mergeable; `main` değiştirilmedi.
- `e55d1df` üzerindeki gerekli GitHub kontrolleri başarılıdır.
- İlk kanonik yürütme-defteri commit'i
  `6336681bc17abaa669b228429f0101076348856f` üzerindeki altı GitHub workflow'u
  da başarılıdır. Yeni bir çalışma head'i oluştuğunda panoda ayrıca
  `current_head` ve `last_ci_pass_head` tutulur; bu iki değer farklıysa durum
  `CI_PENDING` kabul edilir.
- Aktif canlı/current sonuç: 49 kullanılabilir NONFIN valuation, 48 FOLLOW,
  48 gerçek M2, 11 Ek9, 2 Total/ranking (`RGYAS`, `TABGD`) ve 805 açık ret.
- Tarihsel PIT M2 hâlâ 0'dır. Canlı 48 M2 ile tarihsel sonuçlar aynı başarı
  sayacı altında birleştirilemez.

`e55d1df` sabit bir çalışma tabanı değil, doğrulanmış başlangıç referansıdır.
Her yeni çalışma önce `git fetch` yapar. Uzak dal ilerideyse mevcut işi
ezmeden en güncel doğrulanmış head'den devam eder.

## 2. Değiştirilemez güvenlik ve kapsam kuralları

Bu çalışma boyunca, ayrı ve açık bir model-yönetişimi kararı olmadan:

- M1/M2/M3/Ek1/Ek4/Ek9 ağırlıkları değiştirilmez.
- Veto eşiği veya veto faktörü değiştirilmez.
- Minimum peer sayısı ve valuation coverage eşiği gevşetilmez.
- BIST evreni küçültülmez; ticker'lar topluca ya da kalıcı olarak dışlanmaz.
- Eksik/NaN/NULL veri `0.5`, `0` veya başka bir nötr/iyi skorla doldurulmaz.
- `1 TL = 1 pay`, nominal sermaye = pay adedi veya benzeri kanıtsız kabul
  yapılmaz.
- Current ticker/sector/financial değerleri tarihsel PIT kanıtı yerine
  kullanılmaz.
- Ticker değişikliği doğrudan fiyat taşıma izni sayılmaz.
- HOLDING/GYO book-equity proxy'si canonical tarihsel NAV diye etiketlenmez.
- Cutoff-sonrası veri ve signal-day geleceği kullanılmaz.
- Çözülemeyen hücreler sessizce düşürülmez; reason code, kanıt ve denenen
  yollarla explicit rejection veya `BLOCKED` bırakılır.

Bu kurallar kapsam daraltmasını engeller. İş sırasının daraltılması yalnızca
teşhis ve önceliklendirmedir; şirket, dönem, modül veya tarihsel hedef iptali
anlamına gelmez.

## 3. Her iş adımında zorunlu güncelleme protokolü

Her `Wn` işi için aynı sıra izlenir:

1. Başlangıç head'i, temiz/kirli çalışma ağacı ve ilgili artifact hashleri
   kaydedilir.
2. Davranış değiştiren ilk editten **önce** immutable baseline artifact ve
   receipt alınır. Sonradan üretilen "before" çıktısı baseline sayılmaz.
3. Sorun kod yolu ve gerçek çağıran üzerinden yeniden üretilir; yalnız teorik
   iddia düzeltme gerekçesi sayılmaz.
4. Önce pozitif/negatif test ve gerekli mutasyon testi yazılır.
5. En dar güvenli düzeltme uygulanır; değişmeyen sözleşmeler açıklanır. Risk
   gerçek üretim çağrı zincirinden erişilemiyorsa kod sırf teorik temizlik için
   değiştirilmez; `NO_PRODUCTION_REACHABILITY` kanıtı bırakılır.
6. Hedef test, ilgili birleşik test ve davranış değişikliğinde tam regresyon
   çalıştırılır.
7. Önce/sonra artifact karşılaştırması ve provenance etkisi üretilir.
8. Receipt; komut, commit, kaynak SHA, çıktı SHA, kapsam ve ret dağılımını içerir.
9. Küçük ve anlamlı commit push edilir; PR #40 ve Issue #37 aynı adımda
   güncellenir.
10. Gerekli GitHub CI tamamlanmadan iş `DONE` veya teknik kapanış sayılmaz.

Durum değerleri: `TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE`.
`BLOCKED` kaydı; tam hedefi, etkilenen hücreleri, tüketilen repo-içi/ücretsiz
kanıt yollarını, son kanıtı ve yeniden açma koşulunu içermek zorundadır.

## 4. Kesin yürütme ve bağımlılık kararı

```text
ANA HAT — M2 önceliği
W0 durum kilidi
  -> W1 pre-change baseline + canlı fail-closed denetimi/düzeltmesi
      -> W2 canlı sonuç provenance/etki denetimi
          -> W5 tarihsel SMRTG M2 canary
              -> W6 tarihsel M2 kapsam genişletme
                  -> W7-A ilk gerçek tarihsel Total canary
                  -> W7-B artımlı gerçek P4 skor/sıralama
                      -> W8 deneysel P5 60 aylık ledger
                          -> W9 P6 final denetim

PARALEL, ANA M2 HATTINI BLOKLAMAZ
W3 fiyat popülasyonları + ticker lineage -------> W8 seçili işlemler kapısı
                                              \-> W9 tam V24-G kapısı
W4 Ek4 sözleşme denetimi ----------------------> W7-B final kabul kapısı
W6-B BANK coe/macro_cap -----------------------> BANK kapsamı; NONFIN'i bloklamaz
W10 authoritative P7 --------------------------> authoritative etiket; deneysel
                                                  W5-W9 hattını bloklamaz
```

Karar gerekçesi:

- Kullanıcının birincil hedefi 3.017 adet 5/6 hücreyi M2 ile açmaktır. Bu
  nedenle execution-price ve Ek4 araştırması tarihsel SMRTG/M2'nin önüne
  geçirilemez.
- W1 güvenlik işi önce gelir; yeni skor üretmeden önce eksik verinin gerçek
  skora dönüşemediği kanıtlanmalıdır.
- W7 bütün W6 araştırmasının bitmesini beklemez. İlk gerçek tarihsel M2 ile
  canary Total; her yeni güvenli grup ile artımlı P4 üretilir.
- W8 için bütün evrenin fiyatının var olması şart koşulmaz: deneysel portföyde
  yalnız gerçekten seçilen alış/satışların exact execution fiyatı zorunludur.
  Buna karşılık tam V24-G/authoritative readiness bütün gerekli registry ve
  execution-price kapsamını W9'da ister.

## 5. İş paketleri ve kabul ölçütleri

### W0 — Durum ve yönetişim kilidi

Durum: **DONE** (bu defterin ilk sürümü)

- [x] Uzak dal fetch edildi; final head `e55d1df` doğrulandı.
- [x] PR #40 draft/mergeable ve final-head CI sonuçları doğrulandı.
- [x] Yanlış Ek9 devamının geri alındığı ve aktif sayıların 48 M2 / 2 Total
  olduğu kaydedildi.
- [x] Issue #37 ana canlı pano olarak seçildi; yeni rakip issue açılmadı.
- [x] Kapsamı/modeli değiştirmeyen kurallar kilitlendi.

Kabul: Bu belge ve Issue #37 aynı head/durum bilgisini gösterir.

### W1 — Pre-change baseline ve canlı M2/M3/Ek9 fail-closed güvenliği

Durum: **DONE — ANA HAT** (kod/test head'i `7ea6a1f`)

`7ea6a1f` üzerindeki altı CI koşusu başarılıdır. Bu kapanış kaydını taşıyan
yeni head için de Bölüm 1'deki `current_head == last_ci_pass_head` kapısı
zorunludur; eşitlenene kadar panonun durumu `CI_PENDING`, teknik kapanış
beklemededir. Nihai kabul, Issue #37'de exact-head CI kanıtıyla kaydedilir.

W1-0 baseline, üretim kodu değiştirilmeden `0db6cc1` üzerinde alındı:
[baseline.json](../data/audit/w1_live_fail_closed_v1/baseline.json).
48 M2 / 48 FOLLOW / 11 Ek9 / 2 Total / 805 ret korunuyor; baseline yeniden
yazılamaz. W1-A/B/C düzeltildi: Linux/PostgreSQL 2294 PASS/7 SKIP,
Windows 2063 PASS/234 SKIP, BANK 277 PASS/1 XFAIL, W1 mutasyon 6/6 KILLED.
NULL → geçerli skor → yeniden ret veritabanı güncelleme zinciri de geçti.
[W1 denetim raporu](W1_LIVE_FAIL_CLOSED_AUDIT.md),
[mutasyon receipt](../data/audit/w1_live_fail_closed_v1/mutations.json),
[dondurulmuş çıktı kontrolü](../data/audit/w1_live_fail_closed_v1/frozen_outputs.json).
[Kabul receipt'i](../data/audit/w1_live_fail_closed_v1/receipt.json) kaynak ve
çıktı hashlerini, kod head'ini ve altı başarılı CI bağlantısını içerir.
W2 ve W5 başlatılmadı; model/evren/eşik değişikliği yok.

#### W1-0 — Değişiklik öncesi immutable baseline

- Current valuation/FOLLOW/M2/M3/Ek9/Total/ranking artifact'larının mevcut
  dosya hashleri ve satır sayıları kod değişmeden kaydedilir.
- Aktif 48 M2, 11 Ek9 ve iki Total satırı baseline dataset olarak dondurulur.
- Kaynak tablo/kolon nullability envanteri ile çalıştırma komutu receipt'e
  yazılır.
- Bu baseline alınmadan W1-A/B/C davranış değişikliği yapılamaz.

#### W1-A — M2 NULL/NaN çağrı zinciri

- Bütün M2 üretici tabloları ve `run_daily_pipeline` çağıranları çıkarılır.
- Özellikle nullable `analytics.m2_period_comparison.m2_final` ile
  `fillna(0.5)` yolu gerçek bir test kaydıyla doğrulanır.
- Eksik M2, Total'e gerçek skor olarak giremez; explicit rejection/provenance
  sonucu üretir.
- Gerçek `0.5` skoru ile eksik değerin semantik olarak ayrıldığı test edilir.
- Gerçek çağrı zinciri riske ulaşmıyorsa `NO_PRODUCTION_REACHABILITY` olarak
  kanıtlanır ve gereksiz veri modeli değişikliği yapılmaz.

#### W1-B — M3 NULL/NaN çağrı zinciri

- M3'teki eşdeğer `fillna(0.5)` yolları ayrı denetlenir.
- Eksik M3, gerçek nötr skor gibi davranamaz.
- M2 sonucu M3 için kanıt sayılmaz; iki yol ayrı test ve receipt alır.

#### W1-C — Canlı Ek9 seyrek fiyat yolu

- Canlı `_compute_ek9_vol` için minimum gözlem, 63 sonlu getiri, boş/sabit
  seri ve NaN standart sapma testleri yazılır.
- `NaN std -> 0 volatilite -> Ek9=1` yolu kapatılır.
- Tarihsel replay'in 64 fiyat/63 sonlu getiri fail-closed davranışı korunur.
- Canlı risk ile tarihsel `STOCK_WINDOW_PRICE_MISSING` popülasyonu birbirine
  karıştırılmaz.

Kabul:

- NULL/NaN/seyrek veri Total'e skor olarak ulaşamaz.
- Negatif ve mutasyon testleri eski kusurlu yolu kırar.
- Üretim ağırlıkları, eşikler ve geçerli skorların matematiği değişmez.
- Hedef testler, tam regresyon ve gerekli CI başarılıdır.

### W2 — Canlı 48 M2 / 2 Total provenance ve etki denetimi

Durum: **DONE — DÜZELTME DOĞRULANDI**

Başlangıç head'i `f3731ce`; 849 ham KAP raporundan 20.580 kullanılan olgu
yeniden türetildi. 48 M2/FOLLOW ve 11 Ek9 W1-A/B/C'den etkilenmiyor.
Ancak 8 Eylül CORE artifact'ı güncel türetmeyle 130/131 satırda farklı:
RGYAS/TABGD Total girdileri de etkileniyor. Eski üretim commit'i `e93b7c5`
ile 131/131 satır birebir yeniden üretildi; neden, W1 öncesi `fad20cc` akış
türetme düzeltmesinden sonra CORE'un yenilenmemesi olarak doğrulandı.
59 hedef satır UNAFFECTED idi; iki Total güncel-kod uyumu açısından UNRESOLVED
olarak bırakılmıştı. Kullanıcı kalan W2 düzeltmesini yetkilendirdi. Aynı donmuş
kaynak/saat ile CORE yeniden üretildi; iki Total ve ranking yenilendi. RGYAS
46.2949460644 → 46.6021011290, TABGD 40.7156025426 → 43.9061751217; kararlar
UZAK, sıralama ve 807/2/805 kapsamı değişmedi.
[W2 tam denetim ve düzeltme planı](W2_CURRENT_PROVENANCE_AUDIT.md).
[W2 uygulanmış düzeltme kaydı](W2_CURRENT_CORRECTION.md).
[W2 satır denetimi](../data/audit/w2_current_provenance_v1/rows.jsonl) ve
[phase receipt](../data/audit/w2_current_provenance_v1/receipt.json).
Canlı artifact, model ve eşikler değiştirilmedi; W5 başlatılmadı.

Uygulama head'i `6a73096` altı CI kontrolünde PASS. Eski sekiz artifact ayrı
snapshot'ta korunur; yeni assembly receipt düzeltmeyi yeni veri çekimi olarak
sunmaz. Ücretli veri veya yeni tarihsel tarama kullanılmadı. W2 kabulü verildi.

- W1-0 immutable baseline ile her W1-A/B/C alt değişikliğinden sonraki current
  artifact ayrı karşılaştırılır; yalnız toplu son karşılaştırmaya güvenilmez.
- 48 M2, 11 Ek9 ve iki Total satırının hangi tablo/fonksiyon/kaynak satırından
  geldiği izlenir.
- Her satır `UNAFFECTED`, `CHANGED_VALID`, `REJECTED_AFTER_FIX` veya
  `UNRESOLVED` olarak sınıflandırılır.
- Sayıların değişmemesi de hash-bound receipt ile kanıtlanır.
- Tarihsel sonuçlar bu sayılara eklenmez.

Kabul: Eski ve yeni artifact farkı, satır bazında reason/provenance ve toplam
sayılarla yayımlanır; açıklanamayan fark kalmaz.

### W3 — Ayrı fiyat popülasyonları ve ticker lineage

Durum: **DONE — PARALLEL / CLAUDE HATTI** (dal `claude/inspiring-cannon-ecxilb`)

Üç popülasyon kendi üreticisinden kendi anahtarıyla yeniden üretildi ve kesişim
raporu yayımlandı: birleşim **402**, naif toplam 739, **337 mükerrer**.
`EXECUTION ⊆ STOCK_WINDOW` ve `PRICE_MISSING ⊆ STOCK_WINDOW`; birleşim
`STOCK_WINDOW`'a eşit. `PRICE_MISSING \ EXECUTION` tek hücre: `EFOR 2025-11-03`
(lineage sınırında sinyal-günü kapsaması cutoff-öncesi pencereyi kanıtlamıyor).
Düz "402" etiketi üç ayrı pencereyi gizliyordu: Ek9 402, M3 189, Ek4 180; ikisi
de Ek9'un alt kümesi.

174/174 execution hücresi kapalı taksonomide tek nedene atandı:
**162 `TICKER_LINEAGE` + 12 `SOURCE_SYMBOL_GAP`**, `UNRESOLVED_BLOCKED` = 0.
162 hücrede fiyat satırı **vardır**, yalnız `BORSA_LINEAGE_YAHOO_ALIAS`
çözünürlüğünde olduğu için exact-ticker kapısından geçmez; sorun veri yokluğu
değil lineage kabul-edilebilirliğidir. 12 hücre INVES/KLRHO/ASGYO'nun bilinen
Issue #31 boşluğudur ve açık ret olarak kalır.

Beş alias'ın dördünde kimlik, beşinde etkinlik tarihi ve kaynak sembol resmî
Borsa workbook'u + KAP pay sınıfı geçmişiyle kanıtlandı; **kurumsal işlem
sürekliliği 0/5 kanıtlandı** (`ACTION_CONTINUITY_UNPROVEN`). Bu nedenle
**0/5 alias kabul edilebilir, 162 hücre `BLOCKED`**. KERVT→BESLR (2025-06-02) ve
EFORC→EFOR (2025-11-03) Koza kümesinden (2025-11-24) ayrı tutuldu; BESLR için
pay sınıfı gözlemi olmadığından KERVT kimliği de kanıtlanamadı.

Hedef test 37 PASS, mutasyon **11/11 KILLED**, iki bağımsız türetme bayt düzeyinde
aynı. W1/W2 kanıt zinciri korunuyor (131 CORE / 48 M2 / 11 Ek9 / 2 Total / 805 ret).
Üretim kodu, model, ağırlık, veto, eşik ve evren değişmedi; hiçbir hücre düzeltilmedi.

[W3 denetim raporu](W3_PRICE_POPULATIONS_AUDIT.md) ·
kanıt `data/audit/w3_price_populations_v1/` ·
[receipt](../data/audit/w3_price_populations_v1/receipt.json).

Aşağıdaki özgün sözleşme, kabul ölçütlerinin kaydı olarak korunur.

Önce aşağıdaki üç sayı ayrı sözleşme ve anahtarla yeniden üretilir. Kesişim
raporu çıkarılmadan birbirinin yerine kullanılamaz veya toplanamaz:

| Popülasyon | Mevcut sayı | Anlamı | Anahtar | Etkilediği kapı |
|---|---:|---|---|---|
| `PRICE_MISSING` | 163 | P3/P4 materialization içindeki fiyat/M2 kaynak reddi | ticker + cutoff/month | İlgili M2 hücresi |
| `EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING` | 174 | 52 ayda readiness/execution açığı | ticker + signal/execution date | Seçili işlem ve tam V24-G |
| `STOCK_WINDOW_PRICE_MISSING` | 402 | Ek9 lookback penceresi eksiği | ticker + analysis window | Tarihsel Ek9 |

163 hücre ana M2 işinin önüne topluca geçirilmez. W6 sırasında doğrudan gerçek
Total'e dönüşebilecek bir hücreyi bloke ediyorsa hücre bazında ele alınır;
aksi halde paralel fiyat hattında kalır.

174 execution hücresi ticker+signal/execution tarihiyle aşağıdaki tekil
nedenlerden birine atanır:

- `TICKER_LINEAGE`
- `NO_TRADING_SESSION_OR_NO_TRADE`
- `SOURCE_SYMBOL_GAP`
- `CALENDAR_OR_SESSION_GAP`
- `INGESTION_OR_PARSER_GAP`
- `OTHER_EVIDENCED`
- `UNRESOLVED_BLOCKED`

Özel kontroller:

- KOZAA -> TRMET, KOZAL -> TRALT, IPEKE -> TRENJ geçişleri 24 Kasım 2025
  etkinlik sınırı çevresinde incelenir.
- Eşleme yalnız aynı şirket/pay sınıfı kimliği, etkinlik tarihi, kurumsal işlem
  sürekliliği ve kaynak sembolü doğrulanırsa yapılır.
- KERVT ayrı incelenir; Koza lineage'ına dahil edilmez.
- Daha önce listelerde görülen EFORC ve diğer ticker'lar sabit dört istisna
  varsayımı olmadan hücre bazında ele alınır.
- Ücretsiz/resmî Borsa İstanbul, KAP ve repo-içi THB kanıt yolları tüketilir.

Kabul:

- Üç popülasyonun üretici artifact'ı, anahtarı, kesişimi ve farkı yayımlanır.
- 174/174 execution hücresi reason-code exhaustiveness kazanır.
- Düzelen her hücre için kaynak ve tarih kanıtı; çözülemeyenler için ayrıntılı
  `BLOCKED` listesi bulunur.
- Deneysel P5 yalnız seçilen işlemlerin exact fiyatını gerektirir; tam
  V24-G/authoritative kapanışta gerekli 174 kapsamı ve registry eksiksizdir.

### W4 — Ek4 fiyat/getiri sözleşmesi ve asimetri denetimi

Durum: **DONE — PARALLEL / CLAUDE HATTI** (dal `claude/inspiring-cannon-ecxilb`)

Kilitli sözleşme ihlali **kanıtlanmadı**: verdict `COMPLIANT`, 5/5 kontrol
kanıtlı. Canlı DB yolu ile tarihsel replay aynı formülü, aynı hisse tabanını
(`COALESCE(adj_close, close)`) ve aynı ham routed endeks bacağını kullanıyor;
sözleşme sektör bacağını zaten ham olarak kilitliyor ve hisse tabanını
belirtmiyor. Dolayısıyla kod değişikliği yetkisi yok.

Asimetri yalnız audit artifact'ında ölçüldü: sektör bacağı sabit tutulup hisse
bacağı ham kapanışa yeniden tabanlandı. **310/5.820 hücre (%5,33) maddi**;
kalan 5.510 hücrede fark yalnız `adj_close` yuvarlaması (maks. 3,8e-07).
Δskor ortalama 0,0716, medyan 0,0517, **maksimum 0,3312**; 163 hücre >0,05,
81 >0,10, 18 >0,20; 49/60 ay, 103 ticker. Ters yönlü pencere yok — sapma tek
yönlü. Hiçbir Ek4 değeri yeniden yazılmadı.

Tarihsel XU100 fallback yasağı korunuyor; aktif canlı artifact'ları üreten
`materialize_current_market_modules.py` de dated rota ile fail-closed.
**Yeni bulgu:** `run_daily_pipeline::_compute_ek4_momentum` NULL rotayı sessizce
XU100'e çeviriyor, geçerli skor üretiyor ve kullanılan endeksi hiçbir yere
yazmıyor. Aktif sonuçların hiçbiri bu yoldan gelmediği için
`NO_PRODUCTION_REACHABILITY_IN_ACTIVE_ARTIFACT_CHAIN` kanıtıyla kaydedildi,
yamalanmadı.

**W7-B için:** sözleşme kapısı PASS; asimetri açık bir yönetişim kalemi olarak
W7-B final kabulünden önce karara bağlanmalı. Issue #39 kapsamını fiyat-seviyesi
valuation ile sınırlayıp momentumu dışarıda bıraktığı için bu kalem kendi
kaydını gerektirir.

Hedef test 27 PASS, mutasyon **10/10 KILLED**, iki bağımsız türetme bayt düzeyinde
aynı. [W4 denetim raporu](W4_EK4_CONTRACT_AUDIT.md) ·
kanıt `data/audit/w4_ek4_contract_v1/` ·
[receipt](../data/audit/w4_ek4_contract_v1/receipt.json).

Aşağıdaki özgün sözleşme, kabul ölçütlerinin kaydı olarak korunur.

- Hisse tarafındaki `COALESCE(adj_close, close)` ile raw sektör endeksi
  kullanımının mevcut kilitli sözleşmeye uyumu incelenir.
- Raw/raw, adjusted/raw ve mevcut davranışın dönem bazında etkisi yalnız audit
  artifact'ında ölçülür.
- Adjusted close kullanımı tek başına look-ahead kanıtı sayılmaz.
- Teknik düzeltme yalnız kilitli fiyat/getiri sözleşmesi ihlali somut olarak
  kanıtlanırsa yapılır.
- Ekonomik/metodolojik tercih gerekiyorsa kod değişmeden ayrı yönetişim kararı
  açılır.
- Historical current-sector/XU100 fallback yasağı korunur; live fallback'ler
  provenance ile görünür ve fail-closed açısından denetlenir.

Kabul: Sözleşme uyumu kararı, karşılaştırmalı artifact ve testlerle belgelenir;
kanıtsız matematik/model değişikliği yapılmaz.

### W5 — Tarihsel SMRTG M2 canary

Durum: **BLOCKED — KANITLI, YENİDEN AÇMA KOŞULLU (ANA HAT ADIMI TAMAMLANDI)**
· dal `claude/inspiring-cannon-ecxilb` · [PR #41](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/pull/41)
· rapor [W5_SMRTG_M2_CANARY.md](W5_SMRTG_M2_CANARY.md)

Kabul ölçütü karşılandı: canary M2 üretmedi, tam neden ve yeniden açma koşuluyla
`BLOCKED` kaldı. Sonuç canlı 48 M2 / 2 Total'den ayrı artifact'tır; canlı
sonuçlara dokunulmadı.

- **W5-A pay/aksiyon kapısı geçti (5/5).** Pay tabanı ham KAP sınıf tablosundan
  yeniden türetildi: dört gözlemden cutoff'tan 20 dakika önce yayımlanan
  2023-07-31 17:49:42 / 605.880.000 seçildi, 2026-01-30 / 1.817.640.000
  dışlandı; aynı gün olduğu için `(2023-07-31, 2023-07-31]` aralığı boştur.
  Look-ahead kullanılsaydı taban 3× şişerdi.
- **Ek bulgu:** CORE artifact SMRTG için güvensiz `ISSUED_CAPITAL_OVER_NOMINAL`
  rotasıyla 306.000.000 taşıyor — sertifikalı tabanın 1/1,98'i. Kayıtlı
  `market_cap` sertifikalı tabanı izlediği için güvensiz rota **yalnız bu
  ticker için** geçersiz kılınmış sayıldı; başka hiçbir ticker sertifika almaz.
- **W5-B köken: CONFIG, çözülmüş.** Artifact
  `KAP_BULK_GENERAL_HOLDING_CORE_EXACT_V1@1` ile kendi içinde tutarlı; yalnız
  varsayılan config `KAP_NONBANK_CORE_EXAMPLE@1` diyor. Açık sürümlü
  `nonfin_valuation.kap_bulk_exact_v1.json` artifact'ın kendi profilini beyan
  ediyor. Veri kusuru değil, routing/kod kusuru değil; artifact yeniden
  adlandırılmadı, kapı zorlanmadı.
- **W5-C blocker: peer kohortu.** 75 NONFIN aday, **0 güvenli peer**, 74
  güvensiz, tek güvenli aday hedefin kendisi. `minimum_peer_count = 5`
  gevşetilmedi. 59 peer'de aralık kanıtlanamıyor (medyan 810 gün; 28'inde
  aralık içinde bilinen aksiyon var), 15 peer'de cutoff öncesi gözlem yok.
- **Blocker yapısal, bu hücreye özgü değil.** 60 cutoff tarandı: 4.203 NONFIN
  aday hücresinin **0'ında** güvenli pay türetmesi var (4.089
  `ISSUED_CAPITAL_OVER_NOMINAL` + 114 boş); sıfır aralıklı sertifikasyon 56
  cutoff'ta 0, 4 cutoff'ta 1 ticker veriyor (ODAS 2021-08, CEMTS 2023-05,
  SMRTG 2023-07, ALARK 2026-04). Kapı için 6 gerekir → **0/60 erişilebilir**.
- **Yeniden açma:** (1) tarihli, hash'e bağlı, boş olmayan bir aralığı boş
  kanıtlayabilen kurumsal işlem envanteri — W3'ün eksik bulduğu kanıtla aynı;
  (2) bu cutoff için sınırlı: BRSAN (4 gün), QUAGR (7), TUKAS (7), TTRAK (26),
  ZOREN (46) pencerelerinde işlem olmadığının kanıtı. Dördünde Yahoo envanteri
  altı yılda tek satır taşımıyor — kanıt yokluğu, yokluk kanıtı değil.
- Doğrulama: **58 hedef test PASS**, **20/20 mutasyon KILLED**, `--check` bayt
  düzeyinde yeniden üretiyor.

**W7-A tetiklenmedi:** defter W7-A'yı yalnız "gerçek M2 çıkarsa aynı commit
serisinde" öngörüyor; M2 çıkmadığı için W7-A başlatılmadı.

**GENEL yeniden açma koşulu karşılandı (aynı gün, W6 kapsamında).** Gerçek,
kaynağı hash'e bağlı bir KAP kurumsal işlem envanteri üretildi ve peer kapısı
**60/60 cutoff'ta erişilebilir** hale geldi (bkz. §W6,
[W6_CA_GATE_REACHABILITY.md](W6_CA_GATE_REACHABILITY.md)). §5'teki "0/60
erişilebilir" bulgusu bu envanterle aşıldı — W5'in kendisi hâlâ `BLOCKED`
kalır (o cutoff'ta gerçek M2 üretilmedi), ama sistemik blocker artık geçerli
değil.

### W6 — Tarihsel M2 kapsamını mümkün olan en yükseğe çıkarma

Durum: **BLOCKED (ölçek sorunu) — W7-A'nın bulduğu kanıt-tarihi kapısı W7-B
ile gerçek arşiv verisiyle aşıldı; gerçek M2 artık ikinci, bağımsız ve çok
daha sıradan bir kapıda (yetersiz peer sayısı) bekliyor (bkz. W7-B altta)**
· dal `claude/inspiring-cannon-ecxilb` · rapor [W6_CA_GATE_REACHABILITY.md](W6_CA_GATE_REACHABILITY.md)
· düzeltme [W7A_EVIDENCE_DATING_GATE.md](W7A_EVIDENCE_DATING_GATE.md)
· kapı-aşımı [W7B_SPK_BULLETIN_EVIDENCE.md](W7B_SPK_BULLETIN_EVIDENCE.md)

**İKİNCİ DÜZELTME (aynı gün, W7-B):** W7-A'nın bulduğu kapı ("bugün
sorgulanan kaynak koşulsuz reddedilir") hâlâ doğru ve **değişmedi** — ama
kayıtsız şartsız değilmiş: **dönemin kendisinde yayımlanmış** bir kaynak
(bugün yapılan bir sorgu değil) bu kapıyı geçebiliyor. SPK'nın (Sermaye
Piyasası Kurulu) 2005'ten beri arşivlenen haftalık bülteni tam olarak böyle
bir kaynak: her sayı kendi yayım anında basılmış, ayrı tarihli bir belge.
98 bülten (2022-06-09 → 2023-08-31, boşluksuz numaralama) arşivlendi; altı
örnek NONFIN ticker (SMRTG, ZOREN, GESAN, ALFAS, KONTR, ENKAI) için gerçek,
hash-doğrulanmış `PRICE_LEVEL_ACTION_COVERAGE_V1` kanıtı inşa edildi ve
**değiştirilmemiş üretim fonksiyonları** (`PriceLevelActionEvidence.verify`,
`materialize_price_level_market_cap`) çağrıldı: **6/6 hisse için gerçek
piyasa değeri üretildi** — projenin sıfır olmayan bir aralık için ilk
üretim-kabul edilebilir piyasa değeri. Negatif kontroller (erken cutoff,
kurcalanmış kaynak) kapının gerçekten geçildiğini, zayıflatılmadığını
doğruluyor. Ama tam `run_historical_pit_nonfin_m2_replay()` çağrısı hâlâ
0 M2 skoru üretiyor: **ikinci, bağımsız bir kapı** (`minimum_peer_count=5`
her çarpan için ayrı ayrı) altı ticker'la aşılamıyor — PB çarpanı 5/5'e
ulaşıyor ama PE/EV_EBIT/PS ulaşmıyor. W7-A'nın kendi bulgusu ve W6'nın
"60/60 erişilebilir kohort" ölçümü ikisi de doğru kalıyor; W7-B üçüncü, yeni
bir bulgu ekliyor, öncekileri geçersiz kılmıyor.

**AYNI GÜN İÇİNDE DAHA DA NETLEŞTİ:** bu "ölçek sorunu" ilk bakışta
göründüğünden daha derin. Kök sebep kanıt kapısıyla ilgisiz: altı
ticker'ın CORE'daki (2023-06-30) en son çeyreğinde `revenue` 5/6'sında,
`net_income` 3/6'sında, `ebit` 1/6'sında `None` — her biri kendi gerekçe
koduyla (örn. `YTD_PERIOD_START_MISMATCH`), sessiz kayıp değil, CORE'un
fail-closed YTD-türetmesinin bilinçli reddi. Bu cutoff'ta **hiçbir NONFIN
sektörü**, kanıt kapısına hiç dokunmadan, salt CORE'un kendi verisiyle 5
tam-finansallı ticker biriktiremiyor (34 XUSIN'de 3, 24 XUHIZ'de 1). SPK-
bülteni yöntemi kaç ticker'ın kanıtlanabilir olduğunu pratikte sınırsız
genişletiyor; darboğaz artık kanıt değil, CORE'un YTD hizalaması — bkz.
[W7B_SPK_BULLETIN_EVIDENCE.md](W7B_SPK_BULLETIN_EVIDENCE.md) §6/§9.

**ÜÇÜNCÜ DÜZELTME (2026-09-15, W7-C): darboğaz kapatıldı, gerçek M2 üretildi.**
W7-B'nin ölçtüğü tek cutoff'ta (2023-08-31) hiçbir NONFIN sektörü CORE'un
kendi verisiyle `minimum_peer_count=5`'e ulaşamıyordu — ama bu ölçüm tek
cutoff'a özgüydü, evrensel bir tavan değil. TTM-tamlık taraması
`signal_date=2023-08-01`'de XUSIN/PE hücresinde 12 ham aday buldu; SASA
(bayat çapa), HEKTS/IPEKE/KOZAL (çapa yok) ve OYAKC (TRY-dışı nominal
değer) ayıklandıktan sonra **yedi genuine, aynı `anchor_period_end`'li aday**
kaldı: BRSAN, CEMTS, QUAGR, TUKAS, KONYA, VESTL, CCOLA. Her birinin KAP
pay-sertifikasyonu artık **her zaman ham `nominalValueOfShares`/
`nominalValuePerShare` metninden** yeniden hesaplanıyor (önceden hesaplanmış
`derived_shares` alanına hiç güvenilmiyor) — bu, CCOLA'nın 2019-05-14
gözleminde ZOREN'inkiyle aynı sınıftan bir ondalık-ayraç hatası buldu
(~686x şişirme; düzeltilmiş değer, 2016'daki kendi gözlemiyle birebir
örtüşüyor). SPK bülten arşivi 98'den **461 bültene** genişletildi
(2016-06-24 → 2023-08-31, boşluksuz) KONYA/VESTL/CCOLA'nın çok yıllı
boşluklarını kapatmak için — W7-B'nin kendi altı ticker'ı bu genişlemeden
etkilenmedi (hepsinin çapası 2022-06-08 veya sonrası).

Sonuç: 7/7 kanıt kapısını geçti, 7/7 negatif kontrol doğru RET, ve tam
`run_historical_pit_nonfin_m2_replay()` (değiştirilmemiş, W7-B'nin **aynı**
config'iyle) **PE ve PB'yi her ticker için `peer_count=6`'yla kullanılabilir
kılıp `minimum_coverage_weight=0.5`'i karşılıyor: 0 ret, 7 gerçek,
sıfır olmayan M2 skoru** — projenin NONFIN göreli-değerleme yolundan
ürettiği ilk gerçek M2. Kapsam açıkça sınırlı: kapalı yedi ticker'lık bir
örneklem, tam evren koşusu değil. Ayrıntı:
[W7C_REAL_M2_SCORE.md](W7C_REAL_M2_SCORE.md).

**ÖNEMLİ DÜZELTME (aynı gün, W7-A):** Aşağıdaki "peer kapısı 0/60 → 60/60"
bulgusu doğru ve geçerli kalıyor — gerçekten hash'e bağlı kanıt üretildi. Ama
bu kanıtla gerçek M2 üretilmeye çalışılınca üretim kodunun **kendi içinde**,
W6'nın ölçmediği ikinci bir kapı bulundu: `PriceLevelActionEvidence.verify()`
her kanıt kaynağının **kendi yayın tarihinin** cutoff'tan önce olmasını
zorunlu kılıyor. Bugün (2026) toplanan bir kayıt, içeriği ne kadar doğru
olursa olsun, kendi yayın zaman damgasını **bugünün tarihiyle** taşır ve
2023 cutoff'u için koşulsuz reddedilir — bu gerçek üretim koduna karşı test
edilerek kanıtlandı (bkz. W7-A). Sonuç: **üretim-kabul edilebilir peer sayısı
hâlâ her cutoff'ta en fazla 1** (W5'in sıfır-aralık bulgusu), 5 asgari şartın
altında. **Gerçek tarihsel NONFIN M2, peer'e dayalı göreli değerleme
yoluyla, 60 cutoff'un hepsinde hâlâ BLOCKED.**

**Peer kapısı 0/60 → 60/60.** W5'in GENEL yeniden açma koşulu ("tarihli,
hash'e bağlı, boş olmayan bir aralığı boş kanıtlayabilen kurumsal işlem
envanteri") karşılandı:

- Piyasa geneli, tarih sınırlı KAP `disclosure/members/byCriteria` sorgusu
  ile 2016-05→2026-07 arası **gap-free** yakalandı: 595 pencere, 0 hata, 0
  boşluk, 649.244 satır. İlk denemede bulunan 2.000 satırlık kesilme
  (kalabalık ayların yarısını gizliyordu) uyarlamalı pencerelemeyle
  düzeltildi — tamlık varsayılmadı, inşa yoluyla kanıtlandı.
- Eşleştirme `disclosureType` değil **konu metni** üzerinden yapıldı: aynı
  gerçek olay (KLRHO 2023-04-17 sermaye artırımı) hem `CA` hem `ODA` tipinde
  görüldü; yalnız `disclosureType` filtrelemesi gerçek olayları kaçırırdı.
  Fail-closed geniş anahtar kelime kümesi kullanıldı.
- Sonuç: 60 cutoff'un **60'ında** peer kapısı erişilebilir (gereken 6'ya
  karşı cutoff başına 21–36 sertifikalanabilir peer). KLRHO'nun bilinen
  olayı doğru pencereyi engelliyor, komşu pencereyi engellemiyor —
  doğrulandı.
- Doğrulama: 32 hedef test PASS, 11/11 mutasyon KILLED, `--check` Python
  3.11/3.12/3.13'te bayt düzeyinde aynı receipt.

**Bu denetim hiçbir M2 üretmedi ve üretim kodunu değiştirmedi.** Kohort
kapısının artık geçilebilir olduğunu kanıtlar; gerçek tarihsel M2'nin
materyalize edilmesi üretim valuation combiner'ının çağrılmasını, dönem-doğru
revenue/EBIT/FOLLOW türetmesini ve her hücrenin kalan sözleşmelerinden
geçmesini gerektiren **ayrı, daha büyük bir yürütme turu**dur. W6'nın kabul
ölçütü ("Tarihsel gerçek M2 sayısı, cohort dağılımı, rejection dağılımı,
provenance ve deterministik ikinci üretim yayımlanır") henüz karşılanmadı.

W5'in "0/60 erişilebilir" sistemik ölçümü **AUDIT/kanıt düzeyinde** aşıldı
(hash'e bağlı, gerçek envanterle "aksiyon yok" sorusu artık cevaplanabiliyor),
ama **üretim-kabul edilebilirlik düzeyinde** aynı 0/60 kalıyor — W7-A'nın
bulduğu kanıt-tarihi kapısı yüzünden. W6'nın altındaki özgün sözleşme metni
ve öncelik listesi hâlâ geçerlidir; kohort kapısı için gereken kanıt türü
netleşti ama bugünden üretilemiyor.

Öncelik 3.017 adet 5/6 modüllü hücredir. Son doğrulanmış araştırma dağılımı:

- 1.749: cutoff sonrasında yayımlanan kanıt
- 423: cutoff anında kanıtlanmış pay durumu yok
- 1: SMRTG canary
- 778 HOLDING: tarihsel PIT NAV yok
- 66 GYO: tarihsel PIT NAV/portföy yok

Toplam NONFIN 2.173 hücredir; `1.749 + 423 = 2.172`, kalan hücre canary'dir.

İşler:

- Repo içi 199.969 semantic fact, 9.487 sermaye gözlemi, eski artifact'lar,
  Git geçmişi ve ücretsiz KAP/Borsa kaynakları yeniden kullanılabilir indeksler
  üzerinden tüketilir; aynı sonuçsuz geniş tarama tekrarlanmaz.
- Revenue/net income/EBIT dönem/YTD/TTM türetmesi ve gerçek FOLLOW ekseni
  dönem-doğru raporlarla denetlenir.
- Cohort planı cell/year değil, aynı analysis date/period/sector-peer kimliği
  üzerinden çıkarılır.
- Her hücre başarı veya explicit rejection üretir; M2'nin 6.000 hücrede tam
  olması beklenmez.
- Her yeni gerçek M2 kohortu W7-B'ye artımlı olarak aktarılır; çözülemeyen diğer
  hücreler çalışmayı topluca durdurmaz.
- "Yeterli M2" için kanıtsız tek sayı eşiği icat edilmez. Hazırlık katmanları:
  `M2_CANARY_READY` (en az bir gerçek M2), `P4_INCREMENTAL_READY` (en az bir
  altı-modüllü gerçek Total) ve aylık kapsam raporuyla `P5_INPUT_READY` olarak
  ayrı ilan edilir.

Kabul: Tarihsel gerçek M2 sayısı, cohort dağılımı, rejection dağılımı,
provenance ve deterministik ikinci üretim yayımlanır.

### W6-B — BANK 509 hücre için `coe` ve `macro_cap`

Durum: **DONE — PARALLEL / CLAUDE HATTI** (dal `claude/inspiring-cannon-ecxilb`)

`macro_cap` **509/509 çözüldü**: resmî SBB Orta Vadeli Program arşivinden altı
vintage, her biri URL + yayın tarihi + boyut + SHA256 ile bağlı. Beş bağımsız
kontrolün beşi de geçti — her değer kayıtlı GSYH çiftinden yeniden hesaplandı,
hiçbir hücre cutoff sonrası yayın kullanmıyor, her hücre en yeni cutoff-uygun
vintage'ı seçmiş, 509/509 hücre P3'te var ve `historical_family = BANK`.
Kayıtlı sınır: ham PDF baytları 92 MB git şişmesi nedeniyle repoda değil; zincir
yeniden indirmeye karşı denetlenebilir, çevrimdışı bayt doğrulaması yapılamaz.

`coe` **BLOCKED** (`COE_METHODOLOGY_UNVERSIONED_AND_INPUT_LINEAGE_INCOMPLETE`).
Üç ücretsiz yol tüketildi: Borsa İstanbul tarihsel endeks verisi ücretli
datastore'a yönleniyor; TCMB EVDS'te resmî banka CoE serisi yok; TCMB çalışma
tebliği tüm aylık girdilerin Bloomberg terminalinden alındığını beyan ediyor.
Repodaki tarihsiz demo sabiti (0,3705) reddedildi — tek bir günümüz sayısı 60
tarihsel cutoff'a taşınamaz.

**Kritik bulgu:** her iki parametre çözülse bile **0 Total skor açılır**.
509 BANK hücresinin **hiçbiri** 3.017'lik öncelik kohortunda değil (kesişim 0;
en az 4 modüllü kümeyle de kesişim 0). Hücrelerde M3 509, Ek4 509, Ek9 483 var;
**M1, Ek1 ve M2 hepsinde eksik**. M2 çözülse en iyi hücre 3 modülden 4'e çıkar,
Total için 6 gerekir. Asıl blocker CORE tanı katmanı: 509/509 hücre
`NO_CORE_DIAGNOSTICS` (453 `TECHNICAL_CORE_FAMILY_UNSUPPORTED_OR_CONFLICTING`,
56 `OWN_REPORT_STATEMENT_SCOPE_CONFLICT`).

509 hücrenin tamamı açık ret/`BLOCKED` kalır; NONFIN ilerlemesi bloklanmıyor.
Hedef test 24 PASS, mutasyon **11/11 KILLED**, iki bağımsız türetme bayt düzeyinde
aynı. [W6-B araştırma raporu](W6B_BANK_COE_MACRO_CAP_RESEARCH.md) ·
kanıt `data/audit/w6b_bank_coe_macro_cap_v1/` ·
[receipt](../data/audit/w6b_bank_coe_macro_cap_v1/receipt.json).

Aşağıdaki özgün sözleşme, kabul ölçütlerinin kaydı olarak korunur.

- Repo içi ve ücretsiz tarih-doğru kaynaklardan dönemsel `coe` ve `macro_cap`
  yeniden kurulabilirliği incelenir.
- Current varsayım tarihsele taşınmaz; ileri bilgi veya geriye doldurma yoktur.
- Kanıtlanamayan banka hücreleri explicit rejection kalır ve NONFIN ilerlemesini
  bloklamaz.

Kabul: Her parametre için tarih, kaynak, yayın zamanı, dönüşüm ve kullanım
aralığı; aksi halde hücre bazlı `BLOCKED`.

### W7 — Gerçek tarihsel Total ve P4 scored/ranking

Durum: **W7-A BLOCKED BY W5 (canary M2 üretmedi); W7-B W6 İLE ARTIMLI — ANA HAT**

#### W7-A — Canary Total

- İlk gerçek tarihsel M2'nin diğer beş gerçek modülü varsa aynı hücre mevcut
  production combiner'a verilir.
- Başarı tek hücrelik motor/provenance kanıtıdır; 60 aylık backtest başarısı
  diye sunulmaz.

#### W7-B — Artımlı P4 scored/ranking

- Yeterli gerçek M2 taşıyan hücreler mevcut production combiner ile hesaplanır.
- Eksik modül neutral-fill edilmez, ağırlık yeniden dağıtılmaz.
- Veto (`good_count < 5`, factor `0.60`) aynen korunur; etkisi ayrıca raporlanır.
- AL'nin veto altında matematiksel olarak imkânsız olduğu ve İZLE'nin mümkün ama
  zor olduğu sonuç dağılımında görünür kılınır; bu aşamada kural değiştirilmez.
- Aylık sıralama skor azalan, ticker artan deterministik tie-break ile üretilir.

Kabul:

- Canary için en az bir gerçek altı-modüllü Total veya kanıtlı blocker.
- Ölçekli çıktı için 60 ayın her birinde `RANKING_AVAILABLE`,
  `NO_VALID_TOTAL_CASH` veya `BLOCKED` durumu bulunur.
- Gerçek scored/rejected hücreler, aylık sıralamalar, iki bit-level aynı üretim,
  W4 sözleşme kararı, full regression ve CI bulunur.

### W8 — P5 60 aylık gerçek backtest

Durum: **BLOCKED BY W7-B; SELECTED-TRADE PRICE DEPENDS ON W3 — ANA HAT**

- 2021-08..2026-07, `TOTAL_RASYO_MONTHLY_OPEN_V1`, en fazla altı hisse,
  AL/İZLE/UZAK ve mevcut katkı kuralları korunur.
- Trade ledger; alış, satış, pay, nakit, katkı, holdings ve NAV içerir.
- XU100 benchmark, nakit/faiz varsayımı, temettü/kurumsal işlem muhasebesi,
  işlem maliyeti ve vergi kapsamı açıkça belirtilir; varsayılanlar gizlenmez.
- Yalnız nakit kalan aylar performans başarısı diye etiketlenmez.
- Bütün 174 evren hücresinin çözülmesi deneysel P5'i otomatik bloklamaz. Ancak
  seçilmiş/alınmış/satılmış her ticker+tarih için exact execution price şarttır;
  eksikse o ay sessizce başka hisseye kaydırılmaz ve `EXECUTION_BLOCKED` olur.
- P5 durumları ayrı tutulur:
  `P5_STARTED` (en az bir gerçek trade-eligible ranking),
  `P5_60M_LEDGER_COMPLETE` (60 ayın tamamı deterministik trade/cash/blocker),
  `P5_PERFORMANCE_INTERPRETABLE` (seçili işlem fiyatları eksiksiz ve kapsam
  sınırlamaları açık).

Kabul: 60 aylık score/decision snapshot, trade ledger, portfolio NAV, benchmark,
nakit korunumu, look-ahead mutasyonları ve yeniden üretim receipt'i.

### W9 — P6 final denetim ve kapanış kararı

Durum: **BLOCKED BY W8; FULL V24-G ALSO DEPENDS ON W3/W10**

- Source -> module -> Total -> decision -> trade -> NAV lineage bağımsız olarak
  yeniden oynatılır veya hash/contract ile doğrulanır.
- Full regression, BANK v4.7, mutation suite ve gerekli GitHub CI tamamlanır.
- `CURRENT_PROJECT_STATUS.md`, Issue #37, PR #40 ve machine-readable evidence
  aynı final head'e bağlanır.
- Experimental ve authoritative sonuç etiketleri karıştırılmaz.
- Gerekli CI tamamlanmadan teknik kapanış ilan edilmez.
- Deneysel P5 tamamlanması tam V24-G anlamına gelmez. Tam V24-G; gerekli bütün
  execution-price/registry kapsamını ve authoritative kapıları ayrıca ister.

Kabul: `PASS`, `PASS_WITH_EXPLICIT_RISK` veya kanıtlı `BLOCKED` kararı; açık iş
varken `DONE`/V24-G READY iddiası yoktur.

### W10 — P7 authoritative historical-version hardening

Durum: **BLOCKED — PARALLEL / CLAUDE HATTI** (dal `claude/inspiring-cannon-ecxilb`)

Issue #24'ün sekiz kapanış ölçütü 6.000 hücreye karşı **ölçüldü**: 6'sı
karşılandı (kaynak kimliği/hash 5.990/5.990 ve 2.115 farklı bildirim; 60 ay ×
100 hücre; evren bağı; PIT yayın damgaları 5.990/5.990 ve **0 cutoff sonrası**;
6.000/6.000 açık ret; 0 örnek/fixture kaynak). **İkisi karşılanmadı:**
`pit_version_identifiers` (**0/5.990** sürüm tanımlayıcısı; hepsinde
`historical_version_enumeration_complete = false`) ve
`sector_family_input_coverage` (**187 hücrede aile çözülemedi**).

Toplu arşivler bu soruya **yapısal olarak** cevap veremiyor: kurtarılan KORTS
2022 çiftinde (1122417 → 1126845, 437 alanın 4'ü değişmiş, ana ortaklık ve
kontrol gücü olmayan paylar yer değiştirmiş) korunmuş hash'li `KAP_2022_Y.zip`
yalnız yeni bildirimi içeriyor — daha eski sürümün varlığı tespit bile edilemez.

Resmî sorgu baytları olan tek pencerede (2023-03) supersession oranı
**9/423 FR = %2,13**; 2.115 farklı seçili rapora uygulanırsa gösterge olarak
**~45 rapor** maruz olabilir (tek ay örneklemi, garanti değil). Aynı ay doğrudan
kirlenme sondası olarak kullanıldı: 19 düzeltmeden 1'i evren ticker'ına
dokunuyor (BFREN), **0'ı herhangi bir hücrede seçili rapor** →
`contamination_detected = false` (60 ayın 1'i için sınırlı olumsuz sonuç).

Dört yol da derecelendirildi; ücretli kaynak kullanılmadı. **Olumlu bulgu:**
resmî `disclosure/members/byCriteria` endpoint'i, yakalamadan ~3,5 yıl önceki
bir pencere için 9 `DUZELTILEN` satırı döndürdü — tarihsel saklama artık
varsayım değil, gösterilmiş olgu. Yine de BLOCKED: bu ücretsiz rota 60 pencerede
çalıştırılmadı ve çalıştırılsa bile silinmiş/bağlantısız bir sürümün olmadığını
kanıtlayamaz — Issue #24'ün tamlık ölçütü tam olarak budur.

Issue #24 açık kalır; `AUTHORITATIVE_PIT_5Y` etiketi verilemez,
`EXPERIMENTAL_RISK_ACCEPTED_5Y` korunur. Deneysel W5–W9 hattı bloklanmıyor.
Denetim tamamen çevrimdışıdır; ağ erişimi yapılmadı.

Hedef test 27 PASS, mutasyon **11/11 KILLED**, iki bağımsız türetme bayt düzeyinde
aynı. [W10 denetim raporu](W10_P7_VERSION_ENUMERATION.md) ·
kanıt `data/audit/w10_p7_enumeration_v1/` ·
[receipt](../data/audit/w10_p7_enumeration_v1/receipt.json).

Aşağıdaki özgün sözleşme, kabul ölçütlerinin kaydı olarak korunur.

- Superseded historical KAP report sürümlerinin authoritative enumeration yolu
  ücretsiz/resmî kaynaklarla araştırılır.
- Issue #24 closure ölçütleri sağlanmadan sonuç `AUTHORITATIVE_PIT_5Y` diye
  etiketlenmez.
- Ücretli bir kaynağın bulunması ücretsiz/repo-içi yollar tüketilmeden fiziksel
  blocker sayılmaz.

Kabul: Issue #24 kapanış ölçütleri veya tüketilen yollarla ayrıntılı `BLOCKED`.

## 6. Kesin karar kaydı

| Karar | Sonuç |
|---|---|
| Ana öncelik | Canlı fail-closed güvenliği sonrası doğrudan SMRTG ve tarihsel M2 |
| Fiyat eksikleri | 163, 174 ve 402 ayrı popülasyon; ana M2 hattını topluca bloke etmez |
| Ek4 | Paralel sözleşme denetimi; W7-B final kabulünden önce kapanır |
| P4 başlama kapısı | İlk gerçek altı-modüllü tarihsel hücre; W6'nın bütünü beklenmez |
| P5 başlama kapısı | En az bir gerçek trade-eligible aylık ranking |
| P5 execution kuralı | Yalnız seçilen her işlem için exact fiyat zorunlu; eksikse ay blocker |
| Tam V24-G | Gerekli full registry/execution kapsamı ve W10 authoritative sınırı ayrı |
| BANK | Paralel; NONFIN M2 ilerlemesini bloklamaz |
| Model değişikliği | Bu yürütme planı kapsamında yok; ayrı açık kullanıcı kararı gerekir |

## 7. İlerleme kaydı

| Tarih | Head | İş | Durum | Kanıt |
|---|---|---|---|---|
| 2026-09-12 | `6336681` | W0 durum/yönetişim kilidi | DONE | Issue #37/PR #40 senkronize; altı workflow SUCCESS |
| 2026-09-12 | bu belge commit'i | Plan bağımlılık denetimi | DONE | M2 ana hattı düzeltildi; üç fiyat popülasyonu ve artımlı P4/P5 kapıları ayrıştırıldı |

| 2026-09-12 | `f3731ce` | W1 canlı fail-closed kapanışı | DONE | Altı final-head CI PASS; Issue #37 |
| 2026-09-13 | `6a73096` | W2 kaynak/etki denetimi ve düzeltme | DONE | 41 hedef test ve 6/6 final-head CI PASS; eski snapshot korundu |
| 2026-09-13 | `claude/inspiring-cannon-ecxilb` | W3 fiyat popülasyonları + ticker lineage | DONE | 174/174 reason-code; 0/5 alias kabul edilebilir; 37 test, 11/11 mutasyon KILLED |
| 2026-09-13 | `claude/inspiring-cannon-ecxilb` | W4 Ek4 fiyat/getiri sözleşmesi denetimi | DONE | Sözleşme COMPLIANT; 310/5.820 maddi sapma, maks 0,3312; 27 test, 10/10 mutasyon KILLED |
| 2026-09-13 | `claude/inspiring-cannon-ecxilb` | W6-B BANK coe/macro_cap araştırması | DONE | macro_cap 509/509 resmî kaynaklı; coe BLOCKED; unlock üst sınırı 0; 24 test, 11/11 mutasyon KILLED |
| 2026-09-13 | `claude/inspiring-cannon-ecxilb` | W10 P7 sürüm enumeration denetimi | BLOCKED | Issue #24 6/8 ölçüt; 0/2.115 sürüm zinciri; sonda kirlenme yok; 27 test, 11/11 mutasyon KILLED |
| 2026-09-14 | `claude/inspiring-cannon-ecxilb` | W5 SMRTG 2023-08 tarihsel M2 canary | BLOCKED | Pay/aksiyon kapısı 5/5 geçti, profil kökeni CONFIG ve çözülmüş; blocker 0 güvenli peer ve 0/60 erişilebilir cutoff; 58 test, 20/20 mutasyon KILLED |
| 2026-09-14 | `claude/inspiring-cannon-ecxilb` | W6 KAP kurumsal işlem envanteri + peer kapısı ölçümü | KISMEN İLERLEDİ | 595 pencere gap-free yakalandı (649.244 satır); peer kapısı (audit düzeyinde) 0/60 → 60/60; 32 test, 11/11 mutasyon KILLED |
| 2026-09-14 | `claude/inspiring-cannon-ecxilb` | W7-A üretim kanıt-tarihi kapısı denetimi | BLOCKED | Gerçek üretim kodu (`PriceLevelActionEvidence.verify`) çağrılarak test edildi; her kaynağın `published_at <= cutoff` şartı bugünkü hiçbir yakalamayla karşılanamıyor; üretim-kabul edilebilir peer sayısı hâlâ ≤1/cutoff; 10 test PASS |
| 2026-09-14 | `claude/inspiring-cannon-ecxilb` | W6-C rapor-zinciriyle boşluk küçültme (sistem geneli) | GAP_NARROWED_NOT_CLOSED | 4.203 hücrenin 3.030'unda dış çapa var; yalnız %21'inde (648) rapor zinciri doğrulama buluyor; boşluk medyanı 815→534 gün (SMRTG'nin 8 peer'lik örneği temsili değilmiş); 16 test, 8/8 mutasyon KILLED |
| 2026-09-14 | `claude/inspiring-cannon-ecxilb` | W7-B SPK bülteniyle kanıt-tarihi kapısını gerçek arşiv veriyle aşma | PARTIAL_PROGRESS | 98 bülten arşivlendi (boşluksuz); 6/6 örnek ticker gerçek üretim kapısından geçti (hash-doğrulanmış piyasa değeri); negatif kontroller 6/6 doğru RET; tam batch replay hâlâ 0 M2 skoru — ikinci, bağımsız kapı (`minimum_peer_count=5`) altında kalan kök sebep CORE'un kendi YTD-türetme eksikliği (34 XUSIN'de 3, 24 XUHIZ'de 1 tam-finansallı ticker); 21 test, 9/9 mutasyon KILLED |
| 2026-09-15 | `claude/inspiring-cannon-ecxilb` | W7-C W7-B'nin darboğazını kapatıp gerçek M2 üretme | M2_MATERIALIZED | SPK arşivi 461 bültene genişletildi (2016-06-24→2023-08-31, boşluksuz; W7-B'nin 6 ticker'ı etkilenmedi); XUSIN/PE'de 7 genuine aday (BRSAN, CEMTS, QUAGR, TUKAS, KONYA, VESTL, CCOLA) — pay sayısı artık her zaman ham KAP metninden yeniden hesaplanıyor (CCOLA'da ~686x ondalık-ayraç hatası bulundu ve düzeltildi); 7/7 kanıt kapısı geçti, 7/7 negatif kontrol doğru RET, değiştirilmemiş `run_historical_pit_nonfin_m2_replay` PE+PB'yi peer_count=6 ile kullanılabilir kılıp **7 gerçek, sıfır olmayan M2 skoru** üretti (0 ret) — kapsam kapalı 7-ticker örneklem, tam evren değil; 22 test, 11/11 mutasyon KILLED |

**W7-B'nin §9'unda öngörülen iki parçalı sonraki adım artık tamamlandı:**
(1) TTM tamlığı `signal_date=2023-08-01`'de sektör × çarpan bazında tarandı
ve XUSIN/PE hücresi 12 ham aday verdi (2023-08-31'in tek-cutoff ölçümünün
evrensel bir tavan olmadığını doğrulayarak); (2) o hücrenin SPK-bülteni
boşluğu aynı, artık kanıtlanmış yöntemle kapatıldı — bkz. W7-C yukarıda ve
[W7C_REAL_M2_SCORE.md](W7C_REAL_M2_SCORE.md). Darboğaz bu XUSIN/PE
hücresinde kapandı; **60 cutoff'un tamamına, diğer sektörlere ve BANK/HOLDING
ailelerine genişletme hâlâ ayrı, gerçekleştirilmemiş bir iş** (W7-C §10).
W7-A'nın kendi bulgusu (bugün sorgulanan kaynak kayıtsız şartsız reddedilir)
hâlâ doğru ve değişmedi; W7-B ve W7-C onu geçersiz kılmadı, yalnız
niteliksel olarak farklı, dönemin kendisinde yayımlanmış bir kaynak türü
sağladı. W6-C'nin "rapor zinciri boşluğu sıfıra indirmiyor" bulgusu da hâlâ
geçerli — W7-B/W7-C onun yerine geçen ayrı bir yöntem, onu düzeltmiyor.

### Paralel hat sahipliği

**Paralel hat 2026-09-13 itibarıyla kapandı:** W3 DONE, W4 DONE, W6-B DONE,
W10 BLOCKED (kanıtlı, yeniden açma koşuluyla). Dördü de
`claude/inspiring-cannon-ecxilb` dalında yürütüldü; toplam 115 hedef test PASS
ve 43/43 mutasyon KILLED. Hiçbiri ana M2 hattını başlatmadı veya bloklamadı.

**Ana hat 2026-09-14'te aynı dalda devam etti:** W5 kanıtlı `BLOCKED` olarak
kapandı (58 test, 20/20 mutasyon). Dal toplamı böylece **173 hedef test PASS**
ve **63/63 mutasyon KILLED**. Sıradaki zorunlu ana hat adımı: **W6**.

CI Linux tarafında baştan geçti (PostgreSQL'li tam regresyon, BANK v4.7, dört
`--check`, dört mutasyon suite'i). Windows job'ı iki taşınabilirlik kusurunu
ortaya çıkardı ve ikisi de düzeltildi:

1. Denetçiler yol dizesini `str(Path)` ile yazıyordu; Windows'ta ters bölü
   `contract_compliance.json` içine sızıp hash'i değiştiriyordu. Dördü de
   `as_posix()`'e çevrildi.
2. Özet ortalaması `sum()` ile hesaplanıyordu. CPython 3.12 float toplamasını
   Neumaier'e çevirdiği için Linux/3.11 `0.07159288381297305`, Windows/3.14
   `0.07159288381297303` üretiyordu. `math.fsum` doğru yuvarlanmış olduğundan
   her sürümde aynı sonucu verir; ona geçildi.

Her ikisi için de kalıcı koruma eklendi: artifact'ta ters bölü bulunmadığı ve
ortalamanın `math.fsum` ile birebir eşleştiği test ediliyor. Dört denetim de
Python 3.11, 3.12 ve 3.13 altında receipt'i birebir yeniden üretiyor. Bulgular
değişmedi; W4 özetindeki tek fark ortalamanın son bitidir.

CI kapısı: mevcut workflow'ların hiçbiri `claude/**` dallarında tetiklenmiyordu
(hepsi `v24-real-data-work` veya `codex/*` kapsamlı). Bu yüzden paralel hat için
ayrı bir workflow eklendi: `.github/workflows/claude-parallel-line-ci.yml`
(`claude/**` push + `workflow_dispatch`). PostgreSQL'li tam regresyon, BANK v4.7,
beş `--check` yeniden üretimi, beş hedef test dosyası ve beş mutasyon suite'ini
koşar; Codex/Astra workflow'larına dokunmaz.

Karışıklığı önlemek için W3/W4/W6-B/W10 paralel paketleri ve W5 ana hat adımı
`claude/inspiring-cannon-ecxilb` dalında yürütülür ve `codex/astra-v24-finalize`
dalına PR ile gelir. `main`, `v24-real-data-work` ve `codex/*` dallarına bu
hattan doğrudan push yapılmaz. Yeni kanıt yalnız `data/audit/w3_*`, `w4_*`,
`w5_*`, `w6b_*`, `w10_*` dizinlerine yazılır; `data/audit/w1_*` ve `w2_*` salt
okunurdur. W5 önceki `data/audit/smrtg_m2_canary_v1/` receipt'ini de yalnız
okur; onu değiştirmez.
