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

Durum: **BLOCKED — CORE ARTIFACT YENİLEMESİ GEREKLİ**

Başlangıç head'i `f3731ce`; 849 ham KAP raporundan 20.580 kullanılan olgu
yeniden türetildi. 48 M2/FOLLOW ve 11 Ek9 W1-A/B/C'den etkilenmiyor.
Ancak 8 Eylül CORE artifact'ı güncel türetmeyle 130/131 satırda farklı:
RGYAS/TABGD Total girdileri de etkileniyor. Eski üretim commit'i `e93b7c5`
ile 131/131 satır birebir yeniden üretildi; neden, W1 öncesi `fad20cc` akış
türetme düzeltmesinden sonra CORE'un yenilenmemesi olarak doğrulandı.
59 hedef satır UNAFFECTED; iki Total güncel-kod uyumu açısından UNRESOLVED.
[W2 tam denetim ve düzeltme planı](W2_CURRENT_PROVENANCE_AUDIT.md).
[W2 satır denetimi](../data/audit/w2_current_provenance_v1/rows.jsonl) ve
[phase receipt](../data/audit/w2_current_provenance_v1/receipt.json).
Canlı artifact, model ve eşikler değiştirilmedi; W5 başlatılmadı.

Yeniden açma: audit bulgusuna dayanarak CORE → Total/ranking → güncel assembly
receipt yenilemesi yetkilendirilmeli; aynı donmuş kaynaklarla üretilip final
CI geçmelidir. Ücretli veri veya yeni tarihsel tarama gerekmiyor. Ayrıntılı
hücre/alan farkları ve tüketilen kanıt yolları W2 raporunda kayıtlıdır.

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

Durum: **TODO / PARALLEL — W5/W6'YI BLOKLAMAZ**

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

Durum: **TODO / PARALLEL — W7-B FINAL KABUL KAPISI**

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

Durum: **BLOCKED BY W2 — ANA HAT**

- `SMRTG 2023-08` için pay/action kapısını geçen mevcut gerçek kanıt yeniden
  doğrulanır.
- Peer cohort ve `KAP_BULK_GENERAL_HOLDING_CORE_EXACT_V1` ile
  `KAP_NONBANK_CORE_EXAMPLE` derivation-profile uyuşmazlığının veri mi,
  config mi, routing/kod kusuru mu olduğu kanıtlanır.
- Minimum peer=5 ve coverage eşikleri gevşetilmez.
- Gerçek FOLLOW dönemi materialize edilmeden M2 üretilmez.

Kabul: Canary ya gerçek tarihsel M2+provenance üretir ya da tam neden ve yeniden
açma koşuluyla `BLOCKED` kalır. Sonuç canlı 48 M2/2 Total'den ayrı artifact'tır.
Gerçek M2 çıkarsa aynı commit serisinde W7-A canary Total denenir; W6'nın tam
bitmesi beklenmez.

### W6 — Tarihsel M2 kapsamını mümkün olan en yükseğe çıkarma

Durum: **BLOCKED BY W5 INITIAL AUDIT — ANA HAT**

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

Durum: **TODO / PARALLEL RESEARCH**

- Repo içi ve ücretsiz tarih-doğru kaynaklardan dönemsel `coe` ve `macro_cap`
  yeniden kurulabilirliği incelenir.
- Current varsayım tarihsele taşınmaz; ileri bilgi veya geriye doldurma yoktur.
- Kanıtlanamayan banka hücreleri explicit rejection kalır ve NONFIN ilerlemesini
  bloklamaz.

Kabul: Her parametre için tarih, kaynak, yayın zamanı, dönüşüm ve kullanım
aralığı; aksi halde hücre bazlı `BLOCKED`.

### W7 — Gerçek tarihsel Total ve P4 scored/ranking

Durum: **W7-A BLOCKED BY W5; W7-B W6 İLE ARTIMLI — ANA HAT**

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

Durum: **TODO / PARALLEL**

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
| 2026-09-13 | W2 başlangıcı `f3731ce` | W2 kaynak/etki denetimi | BLOCKED | Kaynak denetimi tamamlandı; 130/131 eski CORE ve 2 Total için yetkili artifact yenilemesi gerekiyor |

Sonraki zorunlu adım: **W2 düzeltmesi — onay sonrası CORE → Total/ranking → receipt yenilemesi**.
