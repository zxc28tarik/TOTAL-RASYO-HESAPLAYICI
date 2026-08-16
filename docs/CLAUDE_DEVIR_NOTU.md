# Claude Devir Notu — V23 CLOSED (V23-A + V23-B: RESTATE foundation +
# PIT<->RESTATE reconciliation, report-only)

Bu belge, projeyi devralacak olana aktarılacak teknik özettir. **Hiçbir başarıyı
doğrulamadan kabul etme**; kodu, Git geçmişini ve test çıktılarını esas al.

## Sürüm

```
V21 — Reconciliation-1: Impact Plan ↔ Orkestratör gerçek kümesi (report-only)
Başlangıç: V20 (HEAD d73eef8, 102 commit, 1202 passed)
```

## V21 katmanlama kararı

Reconciliation tek seferde üç işi yapan bir mekanizma DEĞİL, katmanlı kuruldu:

```
V21 (bu aşama) : (b) beklenen etki kümesi ↔ gerçekten yeniden hesaplanan küme
                  — en kritik operasyonel reconciliation
V22 (sonraki)   : (c) modül hattı ↔ Total Rasyo tazelik tutarlılığı
V23 (sonraki)   : (a) PIT ↔ CURRENT_KNOWLEDGE_RESTATE
                  — bilgi-zamanı/audit doğruluğu; operasyonel zincirin
                  ilk güvenlik kapısı DEĞİL, bu yüzden en sona bırakıldı
```

**REPORT-ONLY:** V21 yalnız bulgu üretir. Otomatik düzeltici koşu
BAŞLATMAZ (`detect → classify → persist/report`; `detect → repair` YOK).
Gerekçe: reconciliation mekanizmasının kendisini reconciliation edecek bir
güven katmanı henüz yok; ilk turlarda yanlış pozitifleri görmeden kendi
kendine yeniden hesaplama başlatmak yeni bir hata sınıfı yaratırdı.

Otomatik düzeltmeye geçildiğinde: reconciliation kaynaklı koşu normal
fact-change koşusuyla AYNI kimliğe sahip olmamalı. Öneri:
`run_scope=RECONCILIATION_REPAIR` + ayrı `trigger_reason`/`source_run_id`;
`knowledge_basis` veri anlamını taşımaya devam etmeli, tetikleme sebebi
onun içine yüklenmemeli. **Bu V21'de henüz uygulanmadı**, yalnız karar
olarak kayıtlıdır.

### Bulunan boşluk

`impact_application_run.targeted_ticker_count` yalnız bir SAYI tutuyordu;
`evaluate_readiness()` sonucu (hangi ticker'lar hedeflenmesi GEREKTİĞİ)
hiçbir yerde kalıcı değildi. `sql/035` bunu `impact_application_target`
tablosuyla açıkça kalıcılaştırır — reconciliation'ın "beklenen küme"
kaynağı budur.

### Sözleşme

```
expected = impact_application_target'ta bu application_run_id için kayıtlı küme
actual   = company_total_rasyo_result'ta bu analysis_at'te
           run_id = <orkestratöre verilen run_id> olan küme

MISSING    = expected - actual
UNEXPECTED = actual - expected
STALE      = expected ∩ actual, ama satırın GÜNCEL run_id'si bu
             orkestratör koşusuna ait DEĞİL (başka bir koşu tarafından
             sonradan ezilmiş olabilir)

PASS       = üç küme de boş
MISMATCH   = herhangi biri boş değil
INCOMPLETE = application_run.status hâlâ PENDING (yargı ertelenir)
ERROR      = reconciliation'ın kendisi çalışamadı
```

**Kimlik ayrımı — bulunan gerçek hata:** `company_total_rasyo_result.run_id`
V19 **orkestratörünün** kimliği, `impact_application_run.application_run_id`
ise V20 **impact katmanının** kimliğidir. İkisi farklı kimlik uzayları.
İlk yazımda STALE kontrolü yanlışlıkla `application_run_id`'ye karşı
yapılıyordu ve happy-path senaryosu bile MISMATCH veriyordu. API'ye ayrı
`orchestrator_run_id` parametresi eklenerek düzeltildi; `None` verilirse
STALE kontrolü sessizce yanlış sonuç üretmek yerine açıkça atlanır.

Kalıcılık `impact_plan` deseniyle aynı: idempotent (aynı kimlik + aynı
içerik → satır artmaz, `created_at` tazelenmez) + immutable
(`UPDATE`/`DELETE` trigger ile yasak).

### Mutasyon doğrulaması

```
MISSING tespiti kaldırılsın         → 5 test kırıldı
UNEXPECTED tespiti kaldırılsın      → 3 test
STALE tespiti kaldırılsın           → 1 test
PENDING kontrolü kaldırılsın        → 2 test
MISMATCH yerine hep PASS            → 9 test
orchestrator_run_id yerine
  application_run_id kullanılsın    → 6 test  (gerçek hatanın kendisi)
```

## V23-B kapanış — PIT <-> RESTATE reconciliation (V23 tamamı CLOSED)

**Kapsam:** V23-A'nın ürettiği RESTATE sonuçlarını PIT ile karşılaştırır.
Report-only; otomatik düzeltici koşu yok.

### Kritik sözleşme düzeltmesi (kullanıcı tarafından)

İlk taslakta "M2 nedeniyle hiçbir ticker karşılaştırılamıyorsa
`PASS, fully_verified=false`" öneriliyordu. Kullanıcı bunu **reddetti**:
bu, V21/V22-B'de kapatılan "kanıt yok ama PASS" sınıfını geri getirirdi.

Kilitlenen doğru sözleşme:
```
compared_count == 0  -> status HER ZAMAN INCOMPLETE (mismatch_count'a
                         BAKILMAKSIZIN — bugünkü V23-A gerçekliğinde
                         M2 yüzünden bu HER ZAMAN geçerlidir)
status = PASS         -> compared_count >= 1 VE mismatch_count == 0
status = MISMATCH     -> compared_count >= 1 VE mismatch_count > 0
fully_verified = true -> status=PASS VE compared_count == ticker_count
```
Bu, hem Python katmanında hem **veritabanı CHECK kısıtında** ayrı ayrı
zorlanır (`CHECK (compared_count > 0 OR status IN ('INCOMPLETE','ERROR'))`).

### Bulgular (birbirinden bağımsız, karışmaz)

```
PIT_MISSING        : kanonik PIT satırı yok (veya PIT OK değil)
RESTATE_INCOMPLETE : RESTATE total_rasyo_status != OK (V23-A'nın
                     NO_RESTATE_SOURCE_FOR_M2 dahil)
VALUE_CHANGED       : ikisi de mevcut+OK, final_score farklı
DECISION_CHANGED    : ikisi de mevcut+OK, decision farklı
```

### `restate_vs_pit_comparison` view'i hüküm kaynağı YAPILMADI

View `LEFT JOIN` + `IS DISTINCT FROM` kullanıyor. Bugünkü gerçeklikte
(M2 nedeniyle her RESTATE `YETERSIZ_VERI`) view'in `decision_changed`
sütunu **her zaman `TRUE`** dönüyor — sahte fark. Bu canlı testle
doğrulandı (`test_view_hukum_kaynagi_DEGIL_sahte_fark_YOK` ve E2E
senaryo 2): view'den doğrudan okunan `decision_changed=TRUE` iken,
reconciliation doğru şekilde `mismatch_count=0` üretiyor. Toplayıcı
katmanı view'e hiç dokunmuyor; `company_total_rasyo_result` ve
`company_total_rasyo_restate_result`'ı **doğrudan** sorguluyor.

### V1 sınırı — bilerek çözülmedi, yalnız görünür kılındı

**PIT'in kendisi sonradan meşru biçimde değişebilir** (V20'nin hedefli
yeniden koşusu aynı `(analysis_at,ticker)` satırını PIT bozmadan
güncelleyebilir). Karşılaştırılan PIT satırının `run_id`'si
diagnostics'e taşınır ama şu ayrım **yapılmaz**:

> V23-B farkı tespit eder; farkın PIT'in kendi yeniden hesaplamasından mı
> yoksa gerçek restatement etkisinden mi doğduğunu sınıflandırmaz.

### Mutasyon raporu

```
compared_count==0 kontrolü (EN KRİTİK)     => saf: 2 test, canlı: 5 test,
                                               15000: 700 senaryo, E2E: 4/4
mismatch_count>0 kontrolü kaldırılsın      => saf: 1 test, 15000: 1194 senaryo
RESTATE_INCOMPLETE kontrolü kaldırılsın    => saf: 6 test, canlı: 4 test,
                                               15000: 1076 senaryo, E2E: 2/4
fully_verified zorlaması kaldırılsın       => saf: 1 test, 15000: 2200 senaryo
PIT_MISSING kontrolü kaldırılsın           => EŞDEĞER (pit.exists=False
                                               durumunda total_rasyo_status
                                               da None olduğu için ikinci
                                               bağımsız dal aynı bulguyu
                                               üretiyor — çift koruma)
```

### Yol boyunca bulunan hata sınıfı (altıncı kez)

Yeni tablo `total_rasyo_restate_runs`'a FK verdiği için, o tabloyu
`TRUNCATE` eden 2 test dosyası (V23-A'nın kendi live testi dahil) kırıldı.
Düzeltildi.

## V23-A kapanış — RESTATE production foundation complete

**ADI BİLEREK BÖYLE:** "RESTATE tamamlandı" DENMEZ. Bugün itibarıyla hiçbir
ticker `COMPLETE(OK)` restate sonucuna ulaşamaz — sebep aşağıda.

### Kapsam

`knowledge_cutoff_at`'e göre "o an bilinen bilgiyi" gerçekten seçen
deterministik bir okuyucu/hesaplayıcı/kalıcılık hattı kuruldu. Daha önce
(V19'dan bu yana) bu tablolar yalnız test scriptlerinde elle `INSERT`
edilerek dolduruluyordu — gerçek bir üretim yolu yoktu (V23 ön incelemesinde
bulunan boşluk).

### Bulgu — V19'un tek-parametre tasarımı RESTATE'e uymuyor

V19'un `fetch_module_context(analysis_at=...)`'i TEK bir zaman değerini hem
"hangi gün" (context_asof/asof_date sınırı) hem "ne zamana kadar bilgi
biliniyor" (analysis_at sınırı) için kullanıyor. RESTATE'te bunlar zorunlu
olarak farklı (`knowledge_cutoff_at >= target_analysis_at`). V19'un dosyasına
DOKUNULMADAN, ayrı bir okuyucu (`total_rasyo_restate_reader.py`) yazıldı.

### İkinci, daha derin bulgu — şema kısıtı ilk tasarımı geçersiz kıldı

`module_scores` üzerinde (`sql/017`):
```sql
CHECK (analysis_at IS NULL OR asof_date = (analysis_at AT TIME ZONE 'Europe/Istanbul')::date)
```
`asof_date` her zaman `analysis_at`'in takvim gününe KİLİTLİ — ikisi bağımsız
değil. Bu, ilk okuyucu tasarımını (context_asof→target, analysis_at→cutoff
tek WHERE'de) **imkânsız** kıldı: `analysis_at` daha geç olduğunda `asof_date`
de otomatik geç olur, "hedefi temsil eden ama sonra hesaplanan satır" diye
bir şey veritabanı düzeyinde YOKTUR.

**Düzeltilmiş iki adımlı sorgu:**
```
ADIM 1 (reference CTE) : target_analysis_at'e göre hangi period_end güncel
                         (V19'un normal PIT kuralıyla AYNI, cutoff'a göre DEĞİL)
ADIM 2 (candidates CTE): AYNI period_end için, knowledge_cutoff_at'e kadar
                         gelen TÜM satırlar arasından EN GÜNCELİNİ seç —
                         asof_date hedef günden SONRA olsa bile (bu artık
                         "o dönem için gelen bir DÜZELTME"dir)
```

`analysis_at IS NULL` olan satırlar bu genişletilmiş pencereden HİÇBİR ZAMAN
yararlanamaz (V22-A ilkesinin uzantısı — kanıtlanamayan kimliğe uydurma
imtiyaz verilmez).

### M2 sözleşmesi (kilitlendi, uygulandı)

```
M2 zorunlu modül olmaya devam eder.
M2 için restate kaynağı YOK → ticker sonucu YETERSIZ_VERI.
Neden: NO_RESTATE_SOURCE_FOR_M2.
PIT'teki mevcut M2 değeri fallback olarak ASLA kullanılmaz.
compute_total_rasyo() DEĞİŞTİRİLMEDİ; altı-modül sözleşmesi KORUNDU.
```

Sonuç: **bugün hiçbir ticker `COMPLETE` restate sonucuna ulaşamaz.** M2'nin
cutoff-aware üretim yolu geldiğinde, bu mimari DEĞİŞTİRİLMEDEN `COMPLETE`
üretmeye başlayabilmelidir (`compute_total_rasyo()` çağrısı kod yolunda zaten
hazır, yalnız hiç tetiklenmiyor).

### Kimlik/içerik ayrımı (V20/V21/V22-B ile aynı desen)

```
restate_run_id  = KİMLİK — yalnız sonucu etkileyen girdilerden türer
                  (restate_contract_version + reader_version; DETECTOR_VERSION
                  DEĞİL — RESTATE üretiminde change-impact detector'ı kullanılmıyor)
inputs_sha256   = İÇERİK — tüketilen total_rasyo_restate_module_input özeti
results_sha256  = İÇERİK — üretilen company_total_rasyo_restate_result özeti
```

İki ayrı hash tutuldu (`inputs_sha256` + `results_sha256`) — "sonuç
tesadüfen aynı çıktı ama farklı modül girdileri tüketildi" durumu ayrı
testle kilitlendi (`test_farkli_modul_girdisi_farkli_inputs_sha...`).

Ticker kümesi normalize edilir (tekilleştirilmiş, sıralı, kanonik format);
zaman damgaları UTC'ye çevrilerek serialize edilir — aynı mantıksal istek
her zaman aynı kimliği üretir.

### Sertleştirme

`sql/031`'den bu yana korumasız kalan `total_rasyo_restate_runs` ve
`company_total_rasyo_restate_result` artık immutable (`UPDATE`/`DELETE`
trigger ile yasak) ve rol ayrımlı (`total_rasyo_runtime`: yalnız
SELECT+INSERT, `TRUNCATE` yasak). Yeni `total_rasyo_restate_module_input`
(V22-A'nın RESTATE eşdeğeri) aynı korumayla kuruldu.

### Mutasyon raporu

```
PIT M2'yi fallback yapan implementasyon        => 5 test kırıldı
eksik modül kontrolü tamamen kaldırılsın       => 14 test kırıldı
ticker/tz normalizasyonu kaldırılsın           => 1-2 test kırıldı
iki adımlı SQL: cutoff sınırı daraltılsın      => 1 test kırıldı
iki adımlı SQL: period_end eşleşmesi kaldırılsın => 1 test kırıldı
referans adımı cutoff kullansın (look-ahead)   => EŞDEĞER — context_asof
                                                   sınırı zaten aynı korumayı
                                                   sağlıyor (şema kısıtı
                                                   asof_date'i analysis_at'in
                                                   gününe kilitlediği için)
```

### Yol boyunca bulunan hata sınıfı (beşinci kez)

Yeni `total_rasyo_restate_module_input` tablosu `total_rasyo_restate_runs`'a
FK verdiği için, o tabloyu `TRUNCATE` eden veya elle `INSERT` eden **4
dosya** (test + iki E2E/canlı doğrulama scripti) kırıldı. Hepsi düzeltildi
ve sıfırdan kurulan veritabanında yeniden doğrulandı.

## V22-B kapanış — Module Freshness Reconciliation

**Kapsam:** V22-A'nın kurduğu tüketim-anı/üretici lineage kanıtını
kullanarak, bir Total Rasyo sonucunun beslendiği modül sonuçlarından
DAHA ESKİ veya YANLIŞ hesaplama zincirine ait olup olmadığını raporlar.
Report-only; henüz düzeltici koşu tetiklenmiyor.

### Halef kuralı (kod yazılmadan önce kilitlendi)

```
TOTAL_STALE (M1,M3,Ek1,Ek4,Ek9):
  module_production_lineage'de
    R.ticker=tüketilen.ticker AND R.module=tüketilen.module
    AND R.analysis_at <= total_rasyo.analysis_at   -- ASLA look-ahead DEĞİL
    AND R.analysis_at >  tüketilen.module_analysis_at
  bir satır VARSA.

TOTAL_STALE (M2, ZAYIF PROXY): company_total_rasyo_result'ın AYNI
  (ticker, analysis_at) için GÜNCEL kanonik m2_source_at'i, tüketilenden
  YENİYSE. Yalnız "daha SONRAKİ resmi bir Total Rasyo koşusu M2'yi
  tazeledi mi" sorusunu cevaplar; HAM sektör motoru verisinin tazeliğini
  DEĞİL.

MODULE_LINEAGE_STALE (yalnız identity_known=true): module_production_
  lineage'de AYNI (ticker, module, analysis_at) ETİKETİ için GÜNCEL
  source_version_id tüketilenden FARKLIYSA. M2 için identity_known HER
  ZAMAN false (V22-A); M2 lineage kontrolü HİÇBİR ZAMAN yapılmaz.
```

TOTAL_STALE "daha yeni" arar (farklı `analysis_at`, `total_rasyo.analysis_at`
ile sınırlı — look-ahead koruması burada); MODULE_LINEAGE_STALE "aynı etiket
altında kimlik değişti mi" arar (eşitlik). İkisi asla çakışmaz, birbirinden
BAĞIMSIZ true/false olabilirler.

### PASS / fully_verified ayrımı

`status=PASS` yalnız YÜRÜTÜLEN kontrollerde bulgu olmadığı anlamına gelir.
`fully_verified=true` ANCAK beklenen bütün uygulanabilir kontroller
GERÇEKTEN yapılmışsa verilir. M2'nin lineage'i mimari olarak asla
yapılamadığı için, M2'de yalnız freshness aranır — M2 lineage eksikliği
TEK BAŞINA `fully_verified`'i düşürmez. Diğer beş modülde hem freshness
hem lineage şart koşulur. `INCOMPLETE` ile KARIŞTIRILMAZ: INCOMPLETE
koşunun/verinin henüz yargı vermeye hazır olmadığı durumdur (örn. V22-A
snapshot'i hiç yazılmamış); kimlik altyapısının bilinçli eksikliği ayrı
bir kavramdır.

### Modül bazında kanıt kapsamı

İki genel boolean yerine, her modül için ayrı `freshness_performed` /
`lineage_performed` / neden kodu tutulur
(`analytics.reconciliation_module_check`, PK `(reconciliation_run_id,
module)`). Altı modülden beşinde lineage yapılmış, M2'de yapılamamış bir
sonuç tek bir kaba bayrakla ifade edilmez.

### Yol boyunca bulunan gerçek hatalar

1. **Saf hesaplayıcı M2 istisnasını kendi kendine zorlamıyordu** — yalnız
   çağıranın `identity_known` bayrağına güveniyordu. Toplayıcıda bir hata
   olsaydı, yalnız DB CHECK kısıtı yakalardı. `_build_check` artık M2 için
   lineage'i KENDİSİ engelliyor.
2. E2E'de M2 test payload'ı `m2_source_at` taşımıyordu (`V19.m2cikti()`
   yardımcısı bu alanı hiç set etmiyor), bu yüzden M2 freshness hiç
   yapılamıyordu ve `fully_verified` yanlış `False` çıkıyordu. Test
   payload'ı düzeltildi (üretim kodu değil).
3. Yeni tablolar `total_rasyo_run`'a FK verdiği için, o tabloyu `TRUNCATE`
   eden **8 dosya/script** (V20/V21'in E2E ve canlı doğrulama scriptleri
   dahil) kırıldı. Hepsi düzeltildi ve sıfırdan kurulan veritabanında
   yeniden doğrulandı.

### Mutasyon raporu — sizin istediğiniz üç bağımsız kırılma

| Mutasyon | Saf (izole) | E2E (gerçek zincir) |
|---|---|---|
| Daha yeni modül satırını görmezden gel | kırıldı | kırıldı (13/13→5/6 benzeri) |
| Identity mismatch'i görmezden gel | kırıldı | kırıldı |
| `identity_known=false` iken sahte lineage PASS | kırıldı (izole test) | **EŞDEĞER** — gerçek akışta toplayıcı M2'ye asla yanlış bayrak vermiyor (üç bağımsız katman: V22-A snapshot, toplayıcı, hesaplayıcı) |

## V22-A kapanış — Module Input Lineage foundation

**Kapsam:** V22-B (reconciliation) için gereken tüketim-anı ve üretici-tarafı
lineage kanıtını kurmak. Report-only; henüz reconciliation yapılmıyor.

### Sözleşme

```
sql/036_total_rasyo_module_input.sql
    analytics.total_rasyo_module_input  — TUKETIM-ANI snapshot, immutable
    (yalnız M1,M2,M3,Ek1,Ek4,Ek9; GOOD_COUNT dahil değil)

src/analytics/module_producer_lineage.py
    module_production_lineage'e fan-out (M1,M3,Ek1,Ek4,Ek9 — M2 HARİÇ)
    run_daily_pipeline._upsert_module_scores'a EKLENDİ (additive, V19/V20/
    V21'in kapalı dosyalarına dokunmadan)

src/analytics/total_rasyo_module_input_snapshot.py
    Bağımsız yeniden sorgu + TOCTOU/değer tutarlılığı + gerçek kimlik
    (source_run_key) zorunluluğu
```

### Kritik ayrım: "lineage satırı var" ≠ "source identity biliniyor"

`identity_known=True` için üç koşul birden gerekir:
1. Bağımsız sorgu tam olarak bir satır bulur (analysis_at eşleşir)
2. O satırın değeri, orkestratörün kullandığı değerle eşleşir (aynı
   `analysis_at` etiketi altında sessiz bir düzeltme/backfill olmadığını
   doğrular)
3. **`source_run_key` gerçekten dolu** (yalnız zaman/değer eşleşmesi
   yeterli değildir — bu, gerçek üretim kimliğinin YERİNE geçmez)

`module_scores.source_run_key` yalnız `kap_bank_batch_runs`'a FK'li —
yani **yalnız BANK batch akışı için** gerçek bir kimlik uzayı var. Sıradan
(BANK-dışı) günlük pipeline'da bu alan `NULL` kalıyor ve **sahte/geçici
64-hex kimlik üretilmiyor**. Sonuç: sistemin büyük kısmında
`identity_known=False` olması **beklenen, dürüst** bir durumdur — V22-B bu
farkı gizlemeden görebilmelidir.

M2 için `identity_known` **her zaman** `False` — sektör motorlarının kendi
üretim kimliğini tutan bir tablo yok.

### Yol boyunca bulunan dört gerçek hata

1. İlk TOCTOU kontrolü **erişilemez koddu** (SQL zaten `analysis_at` ile
   filtrelediği için Python'daki eşitlik kontrolü hiç tetiklenmiyordu).
   Gerçek risk: aynı `analysis_at` etiketiyle bir düzeltme koşusu **değeri
   sessizce değiştirebilir**. Değer tutarlılığı kontrolü eklendi.
2. `identity_known=True` ilk sürümde yalnız zaman+değer eşleşmesiyle
   veriliyordu, `source_run_key` `NULL` olsa bile. "Lineage satırı var" ile
   "source identity biliniyor" karıştırılıyordu; sıkılaştırıldı.
3. Yeni tablo `total_rasyo_run`'a FK verdiği için, o tabloyu `TRUNCATE` eden
   **7 test dosyası ve script** kırıldı (102 hata). Hepsi `total_rasyo_
   module_input`'u truncate zincirine ekleyecek şekilde düzeltildi.
4. `test_bank_m2_total_bridge.py`'deki eski (V18 dönemi) bir test, yeni
   `persist_producer_lineage` yan etkisini taklit etmiyordu ve gerçek
   `psycopg2.extras.execute_values` sahte `Cursor` üzerinde çöküyordu.
   Üretim koduna "gerçek bağlantı mı" kontrolü **eklenmedi** — test mock'u
   yeni yan etkiyi de taklit edecek şekilde genişletildi.

### Mutasyon raporu — kategorize

| Mutasyon | Sonuç |
|---|---|
| Değer tutarlılık kontrolü kaldırılsın | **1 test kırıldı** |
| `source_run_key` zorunluluğu kaldırılsın | **1 test kırıldı** |
| M2 için de bağımsız sorgu denensin | **EŞDEĞER** — `_MODULE_COLUMN.get()` ikinci bağımsız koruma sağlıyor |
| Eksik modülde de sorgu denensin | **EŞDEĞER** — SQL'de `NULL` karşılaştırması zaten hiçbir satır döndürmüyor |
| M2 de fan-out edilsin | **6 test kırıldı** |

"Eşdeğer mutasyon" **başarı sayısına katılmadı** ve **başarısızlık olarak da
işaretlenmedi** — ayrı bir kategoridir: mutasyonun davranışı gerçekten
değiştirmediği, bağımsız bir ikinci korumanın aynı hatayı zaten engellediği
anlamına gelir.

## V21 kapanış — üç aşamalı doğrulama

V19/V20 ile aynı kabul standardı: self-audit → E2E → kanıt tutarlılığı →
kapanış commit'i.

### 1) 15.000 senaryolu öz denetim (saf, DB gerektirmez)

```
kume_buyukluk_cesitliligi          2000 / 2000
yalniz_missing                     1500 / 1500
yalniz_unexpected                  1500 / 1500
yalniz_stale                       1500 / 1500
karma_missing_unexpected_stale     2000 / 2000
pending_incomplete                 1000 / 1000
bos_expected_veya_actual           1000 / 1000
yanlis_orkestratör_kimligi         1000 / 1000
stale_check_performed_sozlesmesi   1000 / 1000
idempotent_replay_sozlesmesi       1500 / 1500
immutability_sozlesmesi            1000 / 1000
TOPLAM                            15000 / 15000
```

**Kapsam sınırı (bilerek):** `idempotent_replay` ve `immutability` aileleri
SÖZLEŞME düzeyinde sınanır (aynı girdi → aynı kimlik/SHA; farklı girdi →
farklı SHA) — gerçek veritabanı davranışı (idempotent INSERT, immutable
UPDATE reddi) DEĞİL. Bu, V20'nin kendi öz denetiminin de DB'ye hiç
dokunmaması ile aynı desendir; gerçek DB davranışı
`tests/test_reconciliation_live.py` (canlı pytest) ve E2E'de kanıtlanır.

**Kurulum hatası bulundu ve düzeltildi:** `immutability_sozlesmesi`
ailesinde rastgele seçilen "yeni" ticker bazen zaten kümede olan bir
ticker'a denk geliyor, içerik gerçekte değişmeden kalıyordu (S14006,
S14012). Senaryo, havuzdan kümenin *dışındaki* bir ticker'ı garanti
seçecek şekilde sıkılaştırıldı.

### 2) Gerçek zincir E2E kabul denetimi (4/4)

```
fact değişikliği → impact plan → application target snapshot → readiness
→ orchestrator → total_rasyo_result → reconciliation
```

Dört senaryo ayrı ayrı: tam eşleşme=PASS, bir hedef eksik=MISSING,
fazladan işlenen=UNEXPECTED, yanlış run kimliği=STALE.

**Gerçek bir kapsam hatası bulundu ve düzeltildi:** `fetch_actual_rows`
yalnız *beklenen* ticker listesini sorguluyordu; beklenmedik bir ticker
zaten sorgu listesinde olmadığı için hiç görünmüyordu — UNEXPECTED
YAKALANAMAZDI. `fetch_actual_rows_for_run()` eklendi (run_id'ye göre TÜM
satırları getirir), ama bu da tek başına yeterli değil: yanlış run_id'li
bir satır bu sorguda hiç dönmez ve STALE yerine MISSING gibi görünürdü.
Doğru "actual" kümesi ikisinin **birleşimi**:
`fetch_actual_rows_full()` — beklenen ticker'lar için ticker-filtreli
sorgu (MISSING/STALE için) ∪ run_id-filtreli sorgu (UNEXPECTED için).
E2E senaryo 3 (UNEXPECTED) bu düzeltme olmadan MISMATCH yerine yanlışlıkla
PASS veriyordu; canlı pytest'e de aynı boşluğu ve düzeltmeyi kanıtlayan
iki test eklendi (`test_fetch_actual_rows_ile_UNEXPECTED_YAKALANAMAZ` /
`..._full_ile_UNEXPECTED_YAKALANIR`).

### 3) Kanıt tutarlılığı

`tests/test_audit_evidence_metadata.py` genişletildi: gerçek migration
sayısı (30), devir notu ile son doğrulama raporu arasındaki regresyon
sayısı tutarlılığı, ve artık V21 self-audit/E2E sayılarının da belgeyle
eşleştiği testle kilitli.

### stale_check_performed görünürlüğü

`orchestrator_run_id=None` verilirse STALE kontrolü ATLANIR ve bu
`stale_check_performed=False` ile AÇIKÇA işaretlenir (persisted JSONB'de
de görünür: `diagnostics->>'stale_check_performed'`). Bir `PASS`'in
"üç kontrol de temiz" mi yoksa "STALE kontrolü hiç çalışmadı" mı olduğu
bu alandan ayırt edilebilir; sessiz bir kanıt boşluğu bırakılmaz.

## V20 devri (önceki aşama)

```
V20 — Change-Impact (etki tespiti, readiness bariyeri, hedefli yeniden koşu)
Başlangıç: V19 (HEAD 8bb5b61, 90 commit, 1052 passed)
```

## V20 doğrulama özeti

```
V19 orkestratör öz denetimi       : 15000 / 15000   (DOKUNULMADI)
V20 change-impact öz denetimi     : 15000 / 15000   (DOKUNULMADI)
V20 E2E kabul denetimi            :    13 / 13       (DOKUNULMADI)
V20 canlı PostgreSQL doğrulaması  :    56 / 56       (DOKUNULMADI)
V21 reconciliation öz denetimi    : 15000 / 15000    (DOKUNULMADI)
V21 reconciliation E2E            :     4 / 4        (DOKUNULMADI)
V22-A canlı PostgreSQL testleri   :    25 / 25       (DOKUNULMADI)
V22-B reconciliation öz denetimi  : 15000 / 15000
V22-B E2E                         :     6 / 6
V22-B canlı PostgreSQL testleri   :    10 / 10
Tam regresyon (PG var)            : 1300 passed  (sıfırdan kurulan DB'de doğrulandı)
PostgreSQL olmayan ortam          : 1129 passed, 171 skipped
BANK motoru                       :  277 passed, 1 xfailed
Şema migration                    :    32
Registry                      : 54 edge, 42 V20 trigger, version 1
Şema migration                :    32
```

**Migration sayımı:** `sql/` dizininde 32 dosya var ama bu migration sayısı
**değildir**. Zincir `make core` (1 dosya) + `make migrate` (28 dosya) = **29
şema migration** çalıştırır. Dışarıda kalan üçü:

- `004_fill_sector_group.sql` — data/backfill, ayrı `fill-sector-group` hedefi
- `012_bank_point_in_time_slots.sql` — parametrik sorgu şablonu, hiç çalışmaz
- `014_bank_point_in_time_slots_batch.sql` — parametrik sorgu şablonu

Bu sayım `tests/test_audit_evidence_metadata.py` ile Makefile'a karşı kilitli.

`134 skipped` PostgreSQL gerektiren testlerdir; **`passed` değildir.**

V20 sözleşmeleri: provenance-aware dependency registry · hedef bazlı
leave-one-out ve `eligible_before ∪ eligible_after` · off-by-one (n anchor =
0..n-1 offset) · readiness bariyeri (lineage ile kanıtlanmadan V19
çalışmaz) · immutable + idempotent impact plan · PIT/RESTATE ayrı tablolar ·
migration/runtime rol ayrımı.

Ayrıntı: `docs/TOTAL_RASYO_ORCHESTRATOR_V19.md` (V19) ve bu notun
"Bilinen kapsam sınırı" bölümü (V20).

## V19 devri (önceki aşama)

```
V19 — Total Rasyo Ana Orkestratörü
Başlangıç: V18 (HEAD 4e9e29b, 77 commit, 860 passed)
```

V18 → V19 commit zinciri:

```
c64d1ab  feat: add Total Rasyo orchestrator persistence migration (sql/027)
db69e65  feat: add point-in-time module read contract
f587929  feat: add engine isolation and single-engine ownership
b0979e5  feat: add six-module Total Rasyo combination core
aee4f5c  feat: add atomic authoritative persistence
35bc0c4  feat: add main orchestrator and end-to-end acceptance tests
127b1d5  feat: clarify overall_status and run-scope contracts
6a6661f  test: add 15000-scenario fault-injecting orchestrator self-audit
e370f99  fix: write persistence_status before commit, not after
```

Ayrıntı: `docs/TOTAL_RASYO_ORCHESTRATOR_V19.md`

## V19 devrinde bulunan en önemli hata — KAYIP TASLAKTAKİ YANLIŞ SÖZLEŞME

Önceki oturumda yazılıp **paketlenmeden konteyner kapandığı için kaybolan**
orkestratör taslağı şu ağırlıkları kullanıyordu:

```python
DEFAULT_WEIGHTS = {"m1": 0.30, "m2": 0.45, "m3": 0.25}
```

Bu **yalnızca yer tutucu ağırlık değildi.** Projede zaten kanıtlanmış bir
sözleşme vardı — `src/analytics/total_rasyo_score.py`, ve bu sözleşme beş ayrı
yerde tutarlı olarak kullanılıyordu (`run_daily_pipeline.py`,
`weight_optimizer.py`, `kap_bank_db_workflow.py`,
`kap_bank_batch_persistence.py` ve `analytics.module_scores` tablosunun
sütunları):

```
M2 .40 | M1 .18 | M3 .12 | Ek4 .16 | Ek1 .08 | Ek9 .06  + good_count vetosu
```

Taslak ağırlıkları **Ek4 (.16) + Ek1 (.08) + Ek9 (.06) = toplam ağırlığın
%30'unu tamamen düşürüp** kalan üç modülü kendi içinde yeniden normalize
ediyordu. `good_count_ge8` vetosunu da atlıyordu.

Sonuç aynı ada sahip fakat **farklı bir büyüklük** olurdu: skorlar sessizce
yanlış çıkar, hiçbir test kırılmaz, sapma yalnız gerçek sıralamada görülürdü.

Bu ağırlıklar `FORBIDDEN_DRAFT_WEIGHTS` olarak
`src/analytics/total_rasyo_combine.py` içinde **yasak listesinde** tutulur.
Ayrıca bir AST testi, birleştirme modülünde ikinci bir sayısal ağırlık sözlüğü
tanımlanmasını engeller.

**Ders:** kayıp kodu "çalışıyordu" varsayarak yeniden kurmak, hatayı da yeniden
kurmaktır. Kaybın kendisi bu vakada koruyucu oldu.

V17 → V18 commit zinciri:

```
079e095  fix: treat pandas NaN and pd.NA as missing sector routing values
b34a05e  feat: add financial institution valuation engine
ff4e8dd  feat: add financial institution ingest, migration and batch pipeline
cb42ba0  fix: recognize NaN and pd.NA as missing in optional valuation fields
15ec963  feat: centralize missing-value contract, add FI CLI/Make/self-audit
```

## V17 devrinde bulunan hata

Devir notu **689 passed** diyordu; ölçüm **686 passed, 3 failed** verdi.

Sebep gerçek bir üretim hatasıydı: PostgreSQL `NULL` değeri pandas'a geçerken
`None` **kalmıyor** (pandas 3.x metin sütunlarında `nan`, nullable sütunlarda
`pd.NA`). `sector_routing.route()` yalnız `is not None` kontrol ettiği için
`sector_code`'u boş olan her şirket — ki bu normal, endeksten yönlendirilmesi
gereken durum — evren sorgusunu çökertiyordu. GYO, HOLDING ve NONFIN hatlarının
üçü de etkileniyordu. Dört batch hattı aynı `route()`'u kullandığı için düzeltme
tek noktada yapıldı.

## Bulunan diğer iki sistemik hata

### 1. Opsiyonel alanlarda eksik değer (üç motorda birden)

`_optional_finite` / `_optional_number` yalnız `is None` kontrol ediyordu.
`financial_institution`, `insurance` ve `nonfin` motorları etkileniyordu.

**Merkezîleştirildi:** `src/utils/missing_values.py`. Kopyalanan kontrol,
kopyalanan hata demektir.

Kapsam analizi (`tests/test_missing_value_contract.py` ile kilitli):

| Katman | Durum |
|---|---|
| `financial_institution`, `insurance`, `nonfin` | Riskli → düzeltildi |
| `holding`, `gyo` | Risksiz (opsiyonel veri alanı yok) |
| 8 `ingest`/`api` yardımcısı | Risksiz (JSON alır; orada `None` doğru) |

Sözleşme korundu: **eksik** (`None`/`NaN`/`pd.NA`) ile **geçersiz**
(`inf`, `bool`, bozuk metin) ayrı; ikincisi hata vermeye devam ediyor.

### 2. Kalıcılıkta `with conn:` eksikliği — sessizce yanlış başarı

Alım kalıcılık fonksiyonunda `with conn:` yoktu. CLI
`"persisted_count": 9, "persisted": true` raporluyor ama satırlar **commit
edilmiyor** ve bağlantı kapanınca kayboluyordu. Atomiklik de aynı yapıdan gelir.

**Bu hata yalnız canlı PostgreSQL'de görülebilirdi**; birim testleri sahte
bağlantı kullandığı için hepsi geçiyordu. Artık hem davranış hem kaynak
düzeyinde test var (`tests/test_financial_institution_persistence.py`).

## V18'de eklenen motor

Ayrıntı: `docs/FINANCIAL_INSTITUTION_VALUATION_V18.md`

Özet: `FACTORING` / `LEASING` / `CONSUMER_FINANCE` ayrı emsal havuzları,
PD/DD ana + F/K koşullu, ROE ortalama özkaynak üzerinden, aktif kalitesi
göstergeleri bandı değil güveni etkiliyor, iki eksenli M2, ret defteri,
atomik kalıcılık.

## Doğrulama durumu

```
pandas 3.0.2 + PostgreSQL         : 1052 passed
pandas 3.0.2, PostgreSQL yok      :  975 passed, 77 skipped
pandas 2.2.3 + PostgreSQL         : 1052 passed
pandas 2.2.3, PostgreSQL yok      :  975 passed, 77 skipped
Saf BANK motoru (iki pandas)      :  277 passed, 1 xfailed
Orkestratör öz denetimi           : 15000 / 15000
Motor öz denetimleri (V18)        : beşi de 15.000/15.000 PASS
                                    (insurance, nonfin, holding, gyo,
                                     financial_institution)
```

Dört kombinasyon da HEAD `d92e6a1` üzerinde ölçülmüştür.

`77 skipped`, PostgreSQL gerektiren testlerdir. **Bunlar `passed` değildir**;
veritabanı yokken testler sessizce geçmek yerine atlanır. Dar test sayılarını
(katman başına 29–48) toplam test sayısı gibi raporlama.

### PostgreSQL 16.14 canlı doğrulaması — V19 ORKESTRATÖR

Aşağıdakiler **gerçek PostgreSQL 16.14 üzerinde, ayrı ayrı** kanıtlanmıştır.
Sahte bağlantı testleri bu sınıfın hatalarını yakalayamaz (V18 dersi).

| Kanıtlanan | Nasıl |
|---|---|
| Migration zinciri | `make core && make migrate` — 25 migration hatasız; `sql/027-030` iki kez uygulandı, idempotent |
| Şema kısıtları | 9 CHECK senaryosu: eksik modülle `OK`, nedensiz ret, karar-skor uyumsuzluğu, küçük harf ticker, çakışmada skor — hepsi reddedildi |
| Point-in-time okuma | Gelecek `asof_date` seçilmedi; `analysis_at=23:00` kaydı 20:00 analizine sızmadı; eksik M3 ve eksik `good_count` doldurulmadı |
| **Commit gerçekten oluyor** | Yazımdan sonra bağlantı **kapatıldı**, **yeni bağlantı** açıldı, satırlar geri okundu |
| **Rollback** | Transaction ortasında SQL hatası → üç tabloda da yarım kayıt yok (şirket satırında ve motor satırında ayrı ayrı) |
| **İdempotent run** | Aynı `run_id` + aynı içerik ikinci kez → satır sayısı 1'de kaldı |
| **Run kimliği çakışması** | Aynı `run_id` + farklı içerik → reddedildi (kanonik parmak izi) |
| **Advisory lock** | İkinci bağlantı `lock_timeout` ile `LockNotAvailable` aldı; kilit bırakılınca alınabildi |
| **Başarı → ret** | Eski skor, karar ve `decision` silindi; `YETERSIZ_VERI` kalıcılaştı |
| **Ret → başarı** | Eski `rejection_reason` temizlendi, skor yazıldı |
| Silme kapsamı | Denenmeyen şirket, denenmeyen motor ve önceki kesim korundu |
| Sayaç dürüstlüğü | Rapor sayaçları veritabanından **yeniden sayılan** satırlarla karşılaştırıldı |
| Sütun sözleşmesi | Python tuple ↔ INSERT sütunları ↔ **gerçek tablo şeması** üç yönlü kilitlendi |
| Sır redaksiyonu | Uzun mesaj DB'de ≤500 karaktere indi; `password: hunter2` maskelendi |
| **Gerçek örnek koşu** | Altı motor, 8 şirket, GYO çöktü; bağlantı kapatılıp yeni bağlantıyla okundu |

Örnek koşu çıktısı (yeni bağlantıdan okunmuş):

```
GARAN  BANK       OK              0.6630  IZLE   veto=False
THYAO  NONFIN     OK              0.6070  IZLE   veto=False
GARFA  FINANCIAL  OK              0.5870  IZLE   veto=False
KCHOL  HOLDING    OK              0.5750  IZLE   veto=False
EREGL  NONFIN     OK              0.4218  UZAK   veto=True   (good_count=2)
AKBNK  BANK       YETERSIZ_VERI        -  -      EKSIK_BILESEN
ANSGR  INSURANCE  YETERSIZ_VERI        -  -      MODUL_SATIRI_YOK
AGYO   GYO        MOTOR_COKTU          -  -
overall_status=PARTIAL | 8 şirket, 5 başarılı | persistence_status=OK
```

### V18 motor katmanı canlı doğrulaması

**Doğrulananlar:**

- `make core && make migrate` — 21 migration dosyası hatasız
- 49 tablo + 7 view oluştu
- `sql/026_financial_institution_valuation.sql` tabloları ve view'i oluştu
- Gerçek JSONL yükleme: 9 kayıt yazıldı ve **tabloda görüldü**
- İdempotentlik: aynı dosya ikinci kez yüklenince satır sayısı 9'da kaldı
- Immutable trigger: `UPDATE` ve `DELETE` ikisi de reddedildi
- `CHECK` kısıtları: çeyrek sonu olmayan `period_end` reddedildi
- CLI: bozuk config veritabanına **dokunmadan** reddedildi (çıkış kodu 1)

### DOĞRULANMAYANLAR — gerçek dış veri gerektirir

Orkestratör **sentetik fikstür ve veritabanı verisiyle** doğrulanmıştır.
Aşağıdakilerin hiçbiri kanıtlanmış değildir ve iddia edilmemelidir:

| Doğrulanmayan | Neden |
|---|---|
| Gerçek KAP/MKK endpoint'i | API anahtarı ve canlı erişim yok |
| Gerçek BIST fiyat sağlayıcısı | Kurumsal işlem/bölünme düzeltmesi dahil |
| Tüm-BIST canlı sıralama | Gerçek tüm-evren verisi yok |
| Gerçek geçmiş backtest | Tarihsel veri seti yok |
| Nihai kalibrasyon | Band genişliği, `max_halfwidth`, `minimum_pe_roe`, aktif kalitesi eşikleri ve AL/İZLE/UZAK bantları hâlâ **yer tutucudur** |
| Uçtan uca üretim akışı | Gerçek veriyle değerleme → M2 → Total Rasyo zinciri |

> Orkestratör sentetik ve veritabanı fikstürleriyle doğrulanmıştır; **gerçek dış
> veriyle uçtan uca doğrulanmış sayılmaz.** Canlı BIST sıralaması, gerçek
> yatırım başarısı, nihai kalibrasyon veya production-ready dış veri
> entegrasyonu iddia edilemez.

## Korunması gereken ilkeler

V17 listesindeki 18 ilke aynen geçerlidir. V18'de eklenenler:

19. Eksik değer kontrolü **tek kaynakta** (`src/utils/missing_values.py`);
    motorlara kopyalanmaz.
20. Eksik değer ile geçersiz değer **ayrıdır**; `inf` ve `bool` eksik sayılmaz.
21. Kalıcılık fonksiyonları **`with conn:`** işlem bağlamı kullanır; yalnız
    `with conn.cursor()` commit etmez.
22. Alt tür kodu aile adı değildir; `sector_code_to_family` ile çevrilir.
23. Aktif kalitesi / teknik göstergeler fiyat bandını değil **güveni** etkiler.

V19'da eklenenler:

24. Total Rasyo formülü **tek kaynaktan** gelir: `compute_total_rasyo()`.
    Orkestratör veto, ağırlık ve karar mantığını yeniden yazmaz. Altı modülden
    azını kullanan hiçbir ağırlık kümesi geçerli değildir.
25. Eksik bileşende **ağırlık kalan modüllere dağıtılmaz**; sonuç
    `YETERSIZ_VERI` olur. Veritabanı CHECK kısıtı bunu şema düzeyinde
    imkânsızlaştırır.
26. `good_count_ge8` eksikse **sıfır varsayılmaz** — sıfır varsaymak eksik
    veriyi sessizce vetoya, yani cezaya çevirir.
27. Sektör M2'si otoritatiftir; `module_scores.m2` hiç okunmaz (çift sayım).
28. Modül girdileri şirket başına **tek** `module_scores` satırından gelir;
    farklı tarihli satırlardan toplanmaz, eksik modül eski satırdan
    tamamlanmaz.
29. "Sonuç yok" tek bir durum değildir: `YETERSIZ_VERI`, `MOTOR_COKTU`,
    `CALISTIRILMADI` ve `YONLENDIRME_CAKISMASI` ayrı tutulur;
    `insufficiency_reason` ayrıntıyı taşır.
30. `FAILED` **orkestrasyonun kullanılamaz olması** demektir. Motorlar sağlıklı
    çalışıp veri yetersiz kaldıysa `COMPLETE_NO_RESULTS` kullanılır.
31. **Evren ile hedef küme ayrıdır.** `targeted_tickers` verilirse otoritatif
    silme yalnız hedefi kapsar. Change-impact bunun üzerine kurulacaktır.
32. Çift motor sahipliğinde **sessiz seçim yoktur**; fail-closed davranılır.
33. Testin yeşil olması yetmez: kritik katmanlarda **mutasyon testiyle** testin
    hatayı yakaladığı kanıtlanır.
34. Testler belirli bir kütüphane sürümünün **fiziksel temsilini** sözleşme
    sanamaz. `requirements.txt` `pandas>=2.2` diyorsa testler 2.2.x altında da
    geçmelidir; tek sürümde doğrulayıp genel geçer sunmak taşınabilirliği
    sessizce yok eder.
35. Öz denetim senaryo kimlikleri **deterministiktir** ve `--replay` ile tekrar
    üretilebilir; kimlik→tohum eşlemesi hiçbir CLI seçeneğine bağlı olamaz.

## Bilinen kapsam sınırı — CASH_FLOW

V20 change-impact üç finansal tabloyu tetikleyici kaynak olarak **kabul eder**,
fakat mevcut skor motorlarında nakit akım bağımlılığı **yoktur**:

```
BALANCE_SHEET     -> scoring/change-impact bağımlılıkları VAR
INCOME_STATEMENT  -> scoring/change-impact bağımlılıkları VAR
CASH_FLOW         -> kabul edilen statement type, registry'de 0 scoring edge
```

Bu bir hata değil, sistemin gerçek yetenek sınırıdır. Dolayısıyla:

- Nakit akım fact değişikliği için **boş impact planı doğru davranıştır** ve
  `empty_reason=NO_SCORING_DEPENDENCY` neden koduyla döner.
- Bu, "desteklenmeyen statement" veya "kapsam dışı kaynak" ile **aynı şey
  değildir**; ikisi ayrı neden kodları taşır ve E2E denetiminde ayrı
  senaryolarla kilitlenmiştir.

**Belgelerde ve dış iletişimde "Total Rasyo nakit akım tablosunu skorluyor"
gibi bir iddia BULUNMAMALIDIR.** Doğru ifade: üç tabloyu veri kaynağı olarak
kabul eder, ancak mevcut skor motorlarında cash-flow fact bağımlılığı yoktur.

İleride gerçekten nakit akım tabanlı bir oran/modül eklenirse, registry
kenarı o kodla **birlikte** eklenmeli ve mevcut "0 CASH_FLOW edge" testi
bilinçli olarak değiştirilmelidir.

`DIAGNOSTIC_ONLY` kenarların plan üretmemesi de "bu veri hiçbir yerde
tazelenmeyecek" anlamına gelmez; yalnızca **Total Rasyo yeniden hesaplaması
gerektirmediği** anlamına gelir. Reconciliation / veri kalitesi hattı tanısal
alanları ayrıca tazeleyebilir.

## Bilinen teknik borç

`src/analytics/run_daily_pipeline.py` hâlâ tahmini `fillna` içeriyor
(satır 146, 284, 299, 561):

```python
df.fillna({"m1":0.0, "m2":0.5, "m3":0.5, "ek1":0.0, "ek4":0.5, "ek9":0.5,
           "good_count_ge8":0})
df["m2_source"].fillna("NEUTRAL_FALLBACK")
```

Altı modülün altısı da uyduruluyor ve `good_count_ge8=0` eksik veriyi vetoya
çeviriyor. **Yeni orkestratör bu yolu kullanmaz.** Eski yol bu aşamada
bilinçli olarak değiştirilmedi; kontrollü biçimde devreden çıkarılacak
**teknik borç** olarak işaretlenmiştir. Değişiklik Tarık'ın açık onayını
gerektirir.

## Sıradaki işler

1. Change-impact sistemi (`targeted_tickers` altyapısı hazır)
2. Gece tam evren reconciliation
3. Bütün sektör sonuçlarını tek günlük batch'te çalıştıran DAG
4. `run_daily_pipeline.py` `fillna` yolunun kontrollü kaldırılması
5. Diğer banka dışı finans şirketleri (yatırım ortaklıkları, aracı kurum)
6. Gece tam evren reconciliation
7. Ret ve veri kalitesi dashboard sözleşmesi
8. Backtest ve kalibrasyon altyapısı
9. Tüm BIST evreninde sektör kapsam raporu

**Dış erişim gerektirenler:** gerçek MKK/KAP endpoint'i ve anahtarı, gerçek BIST
fiyat kaynağı, NAD kaynakları, kurumsal işlem verisi, gerçek dağılımla
kalibrasyon.
