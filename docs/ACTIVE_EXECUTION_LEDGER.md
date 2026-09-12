# Total Rasyo — Aktif Kalan İşler ve Yürütme Defteri

Son güncelleme: 2026-09-12  
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
2. Sorun kod yolu ve gerçek çağıran üzerinden yeniden üretilir; yalnız teorik
   iddia düzeltme gerekçesi sayılmaz.
3. Önce pozitif/negatif test ve gerekli mutasyon testi yazılır.
4. En dar güvenli düzeltme uygulanır; değişmeyen sözleşmeler açıklanır.
5. Hedef test, ilgili birleşik test ve tam regresyon çalıştırılır.
6. Önce/sonra artifact karşılaştırması ve provenance etkisi üretilir.
7. Receipt; komut, commit, kaynak SHA, çıktı SHA, kapsam ve ret dağılımını içerir.
8. Küçük ve anlamlı commit push edilir; PR #40 ve Issue #37 aynı adımda
   güncellenir.
9. Gerekli GitHub CI tamamlanmadan iş `DONE` veya teknik kapanış sayılmaz.

Durum değerleri: `TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE`.
`BLOCKED` kaydı; tam hedefi, etkilenen hücreleri, tüketilen repo-içi/ücretsiz
kanıt yollarını, son kanıtı ve yeniden açma koşulunu içermek zorundadır.

## 4. Zorunlu yürütme sırası

```text
W0 durum kilidi
  -> W1 canlı fail-closed denetimi/düzeltmesi
      -> W2 mevcut canlı sonuç etki denetimi
          -> W3 174 fiyat hücresi ve ticker lineage
          -> W4 Ek4 fiyat/getiri sözleşmesi
              -> W5 tarihsel SMRTG M2 canary
                  -> W6 tarihsel M2 kapsam genişletme
                      -> W7 tarihsel Total/P4
                          -> W8 P5 60 aylık backtest
                              -> W9 P6 final denetim

W6-B BANK ve W10 authoritative P7, bağımlılıkları elverdiğinde paralel
araştırılabilir; ana fail-closed zincirini atlatamaz.
```

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

### W1 — Canlı M2, M3 ve Ek9 fail-closed güvenliği

Durum: **TODO**

#### W1-A — M2 NULL/NaN çağrı zinciri

- Bütün M2 üretici tabloları ve `run_daily_pipeline` çağıranları çıkarılır.
- Özellikle nullable `analytics.m2_period_comparison.m2_final` ile
  `fillna(0.5)` yolu gerçek bir test kaydıyla doğrulanır.
- Eksik M2, Total'e gerçek skor olarak giremez; explicit rejection/provenance
  sonucu üretir.
- Gerçek `0.5` skoru ile eksik değerin semantik olarak ayrıldığı test edilir.

#### W1-B — M3 NULL/NaN çağrı zinciri

- M3'teki eşdeğer `fillna(0.5)` yolları ayrı denetlenir.
- Eksik M3, gerçek nötr skor gibi davranamaz.

#### W1-C — Canlı Ek9 seyrek fiyat yolu

- Canlı `_compute_ek9_vol` için minimum gözlem, 63 sonlu getiri, boş/sabit
  seri ve NaN standart sapma testleri yazılır.
- `NaN std -> 0 volatilite -> Ek9=1` yolu kapatılır.
- Tarihsel replay'in 64 fiyat/63 sonlu getiri fail-closed davranışı korunur.

Kabul:

- NULL/NaN/seyrek veri Total'e skor olarak ulaşamaz.
- Negatif ve mutasyon testleri eski kusurlu yolu kırar.
- Üretim ağırlıkları, eşikler ve geçerli skorların matematiği değişmez.
- Hedef testler, tam regresyon ve gerekli CI başarılıdır.

### W2 — Canlı 48 M2 / 2 Total provenance ve etki denetimi

Durum: **TODO**

- W1 düzeltmelerinden hemen önce ve sonra current artifact zinciri yeniden
  üretilir.
- 48 M2, 11 Ek9 ve iki Total satırının hangi tablo/fonksiyon/kaynak satırından
  geldiği izlenir.
- Her satır `UNAFFECTED`, `CHANGED_VALID`, `REJECTED_AFTER_FIX` veya
  `UNRESOLVED` olarak sınıflandırılır.
- Sayıların değişmemesi de hash-bound receipt ile kanıtlanır.
- Tarihsel sonuçlar bu sayılara eklenmez.

Kabul: Eski ve yeni artifact farkı, satır bazında reason/provenance ve toplam
sayılarla yayımlanır; açıklanamayan fark kalmaz.

### W3 — 174 execution fiyat hücresi ve ticker lineage

Durum: **TODO**

Her hücre ticker+cutoff+execution tarihiyle aşağıdaki tekil nedenlerden birine
atanır:

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

Kabul: 174/174 hücre reason-code exhaustiveness; düzelen her hücre için kaynak
ve tarih kanıtı; çözülemeyenler için ayrıntılı `BLOCKED` listesi.

### W4 — Ek4 fiyat/getiri sözleşmesi ve asimetri denetimi

Durum: **TODO**

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

Durum: **TODO**

- `SMRTG 2023-08` için pay/action kapısını geçen mevcut gerçek kanıt yeniden
  doğrulanır.
- Peer cohort ve `KAP_BULK_GENERAL_HOLDING_CORE_EXACT_V1` ile
  `KAP_NONBANK_CORE_EXAMPLE` derivation-profile uyuşmazlığının veri mi,
  config mi, routing/kod kusuru mu olduğu kanıtlanır.
- Minimum peer=5 ve coverage eşikleri gevşetilmez.
- Gerçek FOLLOW dönemi materialize edilmeden M2 üretilmez.

Kabul: Canary ya gerçek tarihsel M2+provenance üretir ya da tam neden ve yeniden
açma koşuluyla `BLOCKED` kalır. Sonuç canlı 48 M2/2 Total'den ayrı artifact'tır.

### W6 — Tarihsel M2 kapsamını mümkün olan en yükseğe çıkarma

Durum: **TODO**

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
- Yeterli gerçek tarihsel M2 oluşur oluşmaz W7 başlatılır; çözülemeyen diğer
  hücreler çalışmayı topluca durdurmaz.

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

Durum: **BLOCKED BY W5/W6**

- Yeterli gerçek M2 taşıyan hücreler mevcut production combiner ile hesaplanır.
- Eksik modül neutral-fill edilmez, ağırlık yeniden dağıtılmaz.
- Veto (`good_count < 5`, factor `0.60`) aynen korunur; etkisi ayrıca raporlanır.
- AL'nin veto altında matematiksel olarak imkânsız olduğu ve İZLE'nin mümkün ama
  zor olduğu sonuç dağılımında görünür kılınır; bu aşamada kural değiştirilmez.
- Aylık sıralama skor azalan, ticker artan deterministik tie-break ile üretilir.

Kabul: Gerçek scored/rejected hücreler, aylık sıralamalar, iki bit-level aynı
üretim, full regression ve CI.

### W8 — P5 60 aylık gerçek backtest

Durum: **BLOCKED BY W7**

- 2021-08..2026-07, `TOTAL_RASYO_MONTHLY_OPEN_V1`, en fazla altı hisse,
  AL/İZLE/UZAK ve mevcut katkı kuralları korunur.
- Trade ledger; alış, satış, pay, nakit, katkı, holdings ve NAV içerir.
- XU100 benchmark, nakit/faiz varsayımı, temettü/kurumsal işlem muhasebesi,
  işlem maliyeti ve vergi kapsamı açıkça belirtilir; varsayılanlar gizlenmez.
- Yalnız nakit kalan aylar performans başarısı diye etiketlenmez.

Kabul: 60 aylık score/decision snapshot, trade ledger, portfolio NAV, benchmark,
nakit korunumu, look-ahead mutasyonları ve yeniden üretim receipt'i.

### W9 — P6 final denetim ve kapanış kararı

Durum: **BLOCKED BY W8**

- Source -> module -> Total -> decision -> trade -> NAV lineage bağımsız olarak
  yeniden oynatılır veya hash/contract ile doğrulanır.
- Full regression, BANK v4.7, mutation suite ve gerekli GitHub CI tamamlanır.
- `CURRENT_PROJECT_STATUS.md`, Issue #37, PR #40 ve machine-readable evidence
  aynı final head'e bağlanır.
- Experimental ve authoritative sonuç etiketleri karıştırılmaz.
- Gerekli CI tamamlanmadan teknik kapanış ilan edilmez.

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

## 6. İlerleme kaydı

| Tarih | Head | İş | Durum | Kanıt |
|---|---|---|---|---|
| 2026-09-12 | `e55d1df` | W0 durum/yönetişim kilidi | DONE | PR #40 CI başarılı; Issue #37 ve bu defter senkronize edilecek |

Sonraki zorunlu adım: **W1 — canlı M2/M3/Ek9 fail-closed denetimi**.

