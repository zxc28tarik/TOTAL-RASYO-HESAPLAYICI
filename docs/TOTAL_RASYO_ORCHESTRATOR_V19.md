# Total Rasyo Ana Orkestratörü — V19

Altı sektör motorunu yalıtılmış çalıştıran, altı modüllü Total Rasyo skorunu
birleştiren ve sonucu atomik/otoritatif olarak kalıcılaştıran orkestrasyon
katmanı.

**Temel:** V18 (HEAD `4e9e29b`, 77 commit, 860 passed).

---

## 1. Total Rasyo sözleşmesi — altı modül

Formül **bu katmanda tanımlı değildir**. Tek ve kanıtlanmış kaynak:

```
src/analytics/total_rasyo_score.py
    MODULE_KEYS     = (M2, M1, M3, Ek4, Ek1, Ek9)
    DEFAULT_WEIGHTS = M2 .40 | M1 .18 | M3 .12 | Ek4 .16 | Ek1 .08 | Ek9 .06
    veto            = good_count_ge8 < 5  →  skor × 0.60
    karar           = ≥0.70 AL | ≥0.55 İZLE | altı UZAK
```

Orkestratör `compute_total_rasyo()` fonksiyonunu **çağırır**; veto, ağırlık ve
karar mantığını yeniden yazmaz. `tests/test_total_rasyo_combine.py` içindeki AST
testi, birleştirme modülünde ikinci bir sayısal ağırlık sözlüğü tanımlanmasını
yasaklar.

### Bileşen kaynakları

| Modül | Kaynak | Not |
|---|---|---|
| **M2** | Şirketin yönlendirildiği **sektör motoru** | Otoritatif |
| M1, M3, Ek4, Ek1, Ek9 | `analytics.module_scores` (point-in-time) | Tek satır |
| `good_count_ge8` | `analytics.module_scores` (aynı satır) | Veto girdisi |

**Çift sayım yasağı:** `analytics.module_scores.m2` alanı SELECT listesine hiç
alınmaz. Sektör M2'si o alanın yerine geçer. İkisini birlikte puanlamak aynı
sinyali iki kez ağırlıklandırmak olurdu.

### Eksik bileşen

Altı modülden herhangi biri **veya** veto girdisi eksikse:

- nötr değer verilmez,
- ağırlık kalan modüllere **dağıtılmaz**,
- eski veya gelecekteki kayıtla tamamlanmaz,
- `fillna` kullanılmaz.

Sonuç `YETERSIZ_VERI` olur ve eksik modüller açıkça listelenir. Dolu modüller
raporda görünmeye devam eder.

---

## 2. Point-in-time modül okuma

`src/analytics/total_rasyo_module_reader.py`

**Zaman kuralı:** kesim eşitliği aranmaz. Her kayıt için
`kaynak_zamanı <= analysis_at` uygulanır ve gelecekteki veri sızmaz. Koruma iki
katmanlıdır: SQL filtreler, Python yeniden doğrular. Tek katmanlı güven sessiz
sızıntıya açık kapı bırakır.

**Satır bütünlüğü:** şirket başına `row_number()` ile **tek** satır seçilir ve
altı modül girdisinin tamamı o satırdan alınır. Modülleri farklı tarihli
satırlardan toplamak, hiçbir gün birlikte var olmamış bir modül kümesini tek
skora çevirmek olurdu.

**Bayatlık:** `max_context_age_days` (varsayılan 120) dışındaki kayıt seçilmez.

**Eksik/geçersiz sınırı:** `src/utils/missing_values.py` kullanılır.
`None`/`NaN`/`pd.NA` → eksik; `inf`/`bool`/boş metin/aralık dışı → hata.

`good_count_ge8` eksikse **sıfır varsayılmaz**. Sıfır varsaymak
`good_count < eşik` koşulunu doğurur ve skoru 0.60 ile çarpar; yani eksik veri
sessizce cezaya dönüşürdü.

---

## 3. Motor yalıtımı ve tek motor sahipliği

`src/analytics/total_rasyo_engine_isolation.py`

Bir sektör motoru çöktüğünde diğerleri devam eder; çöken motora yönlenen
şirketler rapordan kaybolmaz. `KeyboardInterrupt` ve `SystemExit` yakalanmaz —
bunlar operatörün durdurma iradesidir, motor hatası değildir.

**Tek motor sahipliği fail-closed'dır.** Sessiz seçim yoktur: ne "ilk gelen
kazanır" ne öncelik sırası. Üç çakışma biçimi yakalanır:

1. iki motor aynı şirket için başarılı sonuç üretir,
2. yönlendirme bir aile der, sonucu başka motor üretir,
3. yönlendirmede hiç olmayan şirket için sonuç üretilir.

Çakışan şirket skorsuz `YONLENDIRME_CAKISMASI` durumuna alınır. Sonuç motor
sırasından bağımsızdır.

**Hata mesajı:** kanonik, ≤500 karakter, hassas değer içermez. Anahtar adı
parçası da sayılır (`DB_SECRET` içinde `\bsecret\b` eşleşmez, çünkü `_` kelime
karakteridir) ve değer satır sonuna kadar silinir (`\S+` kullanmak
`Authorization: Bearer xyz123` örneğinde asıl sırrı bırakırdı).

---

## 4. Durum taksonomisi

Şirket durumları **ayrık ve tam kapsayıcı**:

| Durum | Anlam |
|---|---|
| `OK` | Altı modül + veto girdisi tam, skor üretildi |
| `YETERSIZ_VERI` | Bileşen eksik; ayrıntı `insufficiency_reason`'da |
| `MOTOR_COKTU` | Motor çağrıldı ve çöktü |
| `CALISTIRILMADI` | Motor bu koşuda hiç çalıştırılmadı |
| `YONLENDIRME_CAKISMASI` | Çift motor sahipliği |

`insufficiency_reason` (`sql/029`): `M2_YOK`, `MODUL_SATIRI_YOK`,
`EKSIK_BILESEN`, `MOTOR_REDDETTI`, `DEGERLEME_KULLANILAMAZ`, `HESAP_HATASI`.

Bu ayrım olmadan "motor bu şirketi reddetti" ile "şirketin geçmiş modül verisi
hiç yok" ayırt edilemez ve teşhis imkânsızlaşır.

---

## 5. `overall_status` — deterministik sözleşme

Ayırıcı soru: **orkestrasyon mu bozuldu, yoksa orkestrasyon çalıştı da veri mi
yetersizdi?**

| Statü | Koşul |
|---|---|
| `FAILED` | Motor var ama hiçbiri sağlıklı çalışmadı; veya kalıcılık başarısız |
| `PARTIAL` | Bazı motorlar çöktü/atlandı veya bazı şirketler çökme/çakışma yüzünden düştü |
| `COMPLETE_NO_RESULTS` | Bütün motorlar sağlıklı, hiçbir şirket skor almadı |
| `COMPLETE` | Motorlar sağlıklı, en az bir şirket skor aldı |

Tek motor hatası bütün koşuyu `FAILED` yapmaz — çalışan motorların sonuçları
geçerlidir; ama `COMPLETE` de gösterilmez, kısmilik gizlenemez.

`COMPLETE_NO_RESULTS` V19'da eklendi. Öncesinde "motorlar sağlıklı çalıştı ama
veri yetersiz" durumu `FAILED`'a düşüyordu ve "sistem bozuk" ile "veri yok"
ayırt edilemiyordu; operatör yanlış yere bakardı.

---

## 6. Evren, hedef küme ve `not_run_policy`

**`routing` evreni tanımlar. `targeted_tickers` bu koşunun hedefini tanımlar.**
İkisi ayrıdır.

Günlük tam koşuda çakışırlar (`run_scope=FULL_UNIVERSE`). Change-impact
koşusunda yalnız birkaç şirket hedeflenir (`run_scope=TARGETED`); o zaman
otoritatif silme **yalnız hedeflenen kümeyi** kapsar ve gereksiz motor
çağrılmaz. İkisini yapıştırmak, birkaç şirketlik bir koşunun bütün kesimi
yeniden yazmasına yol açardı.

Evrende olmayan hedef reddedilir: yönlendirmesi bilinmeyen şirkete hangi
motorun bakacağı belli değildir.

**`not_run_policy`** — bilinçli çalıştırılmayan motorun eski sonucuna ne olacağı:

| Politika | Davranış |
|---|---|
| `OVERWRITE` (varsayılan) | `CALISTIRILMADI` yazılır, eski skor silinir |
| `PRESERVE` | Şirkete hiç dokunulmaz, eski sonuç kalır |

Varsayılan `OVERWRITE`: sessizce duran bayat skor, görünür bir "çalıştırılmadı"
kaydından daha tehlikelidir.

`PRESERVE` **yalnız çalıştırılmayan motor içindir.** Motor çağrılıp çöktüyse
veya şirket `YETERSIZ_VERI` aldıysa eski sonuç yine değişir.

---

## 7. Atomik ve otoritatif kalıcılık

`src/analytics/total_rasyo_persistence.py`

**`with conn:` zorunludur.** psycopg2'de işlemi commit eden yapı budur. Yalnız
`with conn.cursor()` kullanmak INSERT'leri çalıştırır, sayacı doğru doldurur,
hata vermez — ve bağlantı kapanınca her şey kaybolur. V18'de tam olarak bu oldu:
CLI `persisted_count=9` bildirdi, tabloda 0 satır vardı; sahte bağlantı kullanan
bütün birim testleri geçiyordu.

**Otoritatif silme kapsamı:** `analysis_at` + *bu koşuda gerçekten denenen*
ticker/motor kümesi. Kesim genelinde silmek, o koşuda hiç denenmemiş şirketlerin
sonuçlarını da yok ederdi.

**Denenmiş kümesi**, çöken motora yönlenen şirketleri de içerir; böylece eski
başarılı skorları `MOTOR_COKTU` ile değişir.

**Sütun sözleşmesi tek kaynaktan:** sütun listeleri tek yerde tanımlanır, INSERT
metni ve satır tuple'ları ikisi de ondan üretilir. Elle yazılmış iki liste
zamanla kayar ve sessizce yanlış alana yazar.

**Run kimliği:** aynı `run_id` farklı içerikle yeniden kullanılamaz (kanonik
`payload_sha256` parmak izi); aynı içerikle idempotent kabul edilir. Parmak izi
zaman damgalarını dışlar — aynı girdi aynı izi vermelidir, yoksa kontrol her
koşuda tetiklenir ve işe yaramaz.

**Eşzamanlılık:** `pg_advisory_xact_lock` ile aynı kesim serileştirilir. Kilit
transaction kapsamlı olduğu için uygulama çökse bile sızmaz.

**`persistence_status` yazımdan önce konur.** Transaction commit olduysa
kalıcılık zaten başarılıdır; olmadıysa satırın kendisi yoktur. Üçüncü ihtimal
yoktur. (V19'da düzeltildi: sonradan set etmek tabloda kalıcı `NULL` bırakıyordu.)

---

## 8. Şema

| Migration | İçerik |
|---|---|
| `sql/027` | `daily_engine_run`, `company_total_rasyo_result`, 3 view |
| `sql/028` | `total_rasyo_run` kayıt defteri, `run_id` sütunları |
| `sql/029` | `insufficiency_reason` taksonomisi |
| `sql/030` | `COMPLETE_NO_RESULTS`, `run_scope`, `not_run_policy` |

Migration'lar **append-only**. Uygulanmış bir dosyayı değiştirmek, çalıştıran ile
çalıştırmayan kurulumlar arasında sessiz şema farkı üretir.

Veritabanı düzeyindeki kritik kısıtlar:

- `total_rasyo_status='OK'` ancak altı modül + veto girdisi tamsa yazılabilir —
  eksik modülle skor üretilip ağırlığın sessizce dağıtılması **veritabanı
  düzeyinde imkânsızdır**.
- `OK` olmayan her satır `rejection_reason` taşımak zorundadır.
- `(final_score IS NULL) = (decision IS NULL)`.
- Alt sayaçların toplamı `company_count`'a eşittir.
- `universe_company_count >= company_count`.

---

## 9. Doğrulama

| Ortam | Sonuç |
|---|---|
| pandas 3.0.2 + PostgreSQL | **1052 passed** |
| pandas 3.0.2, PostgreSQL yok | 975 passed, **77 skipped** |
| pandas 2.2.3 + PostgreSQL | **1052 passed** |
| pandas 2.2.3, PostgreSQL yok | 975 passed, **77 skipped** |
| BANK motoru (her iki pandas) | **277 passed, 1 xfailed** |
| Orkestratör öz denetimi | **15000 / 15000** |

Dört kombinasyon da HEAD `d92e6a1` üzerinde ölçülmüştür.

`requirements.txt` `pandas>=2.2` diyor; testler **iki sürümde de** doğrulanır.
Testler belirli bir pandas sürümünün fiziksel eksik-değer temsilini
(`None`/`NaN`/`pd.NA`) zorunlu kılamaz — bir AST koruma testi bu kalıbı
yakalar.

### Atlanan test sayısı neden ortama göre değişir

`77 skipped`, PostgreSQL gerektiren iki modülden gelir
(`test_total_rasyo_orchestrator.py` 48, `test_total_rasyo_persistence.py` 29).

Bu sayı **psycopg2'nin kurulu olup olmamasına göre görünüm değiştirir**:

- psycopg2 kurulu, veritabanı yok → her test tek tek atlanır: **77 skipped**
- psycopg2 hiç kurulu değil, modüller **tek başına** çalıştırılır →
  `pytest.importorskip` modül düzeyinde devreye girer: **2 skipped**
- psycopg2 hiç kurulu değil ama **tam takım** çalıştırılır → beş M2 köprü testi
  (`test_bank_m2_total_bridge.py` vb.) `sys.modules["psycopg2"]`'ye sahte modül
  yerleştirdiği için `importorskip` geçer ve testler yine tek tek atlanır:
  **77 skipped**

Üç durumda da **`passed` sayısı 975'tir ve hiçbir test sahte bağlantıya karşı
geçmez.** Farklı `skipped` sayısı bir tutarsızlık değil, aynı atlamanın farklı
granülerlikte raporlanmasıdır. Karşılaştırmada esas alınacak sayı `passed`tır.

`77 skipped`, PostgreSQL gerektiren testlerdir. **Bunlar `passed` değildir** ve
öyle sayılmamalıdır; veritabanı yokken testler sessizce geçmek yerine atlanır.

### Öz denetim dağılımı

```
tam_basarili                     3000    yonlendirme_catismasi           1000
tek_motor_cokmesi                2000    yeniden_calisma                 1000
coklu_motor_cokmesi              1000    sira_degismezligi               1000
eksik_modul_kombinasyonu         2000    kalicilik_hata_enjeksiyonu       750
zaman_kesimi_ve_satir_butunlugu  1500    sinir_deger_ve_config_bypass     750
                                         karma_yuk                       1000
```

Her senaryoda on invariant doğrulanır; "exception olmadı" başarı sayılmaz.
Senaryo kimlikleri deterministiktir:
`python3 -m src.analytics.total_rasyo_self_audit --replay S00042`

---

## 10. Mutasyon kanıtları

Test yeşil olması yetmez; testin hatayı **yakaladığı** kanıtlanmıştır.

| Mutasyon | Kırılan test/senaryo |
|---|---|
| `with conn:` → `if True:` | 13 test |
| Silme kapsamı kesim geneline yayıldı | 1 test |
| `run_id` parmak izi kontrolü kaldırıldı | 1 test |
| Evren başarılı motor çıktısından kurulsun | 8 test |
| Modül satırı yoksa boş bağlam uydurulsun | 1 test |
| Kısmilik gizlensin (hep `COMPLETE`) | 3 test |
| Yönlendirme çakışması yok sayılsın | 1 test |
| Modüller farklı satırlardan toplansın | 2 test |
| `module_scores.m2` M2 olarak okunsun | 25 test |
| Veri yetersizliği `FAILED` sayılsın | 2 test |
| Hedef küme yok sayılsın | 2 test |
| `PRESERVE` yok sayılsın | 1 test |
| Eksik bileşenle skor üretilsin | 80 senaryo |
| İki zaman filtresi de kaldırıldı | 100 senaryo |
| Çakışma yok sayılsın (öz denetim) | 51 senaryo |

---

## 11. Kullanım

```python
from src.analytics.total_rasyo_orchestrator import run_total_rasyo_orchestrator

rapor = run_total_rasyo_orchestrator(
    conn,
    analysis_at=kesim,              # timezone bilgili, zorunlu
    routing={"GARAN": "BANK", ...}, # evren
    engine_runners={"BANK": ...},   # aile -> çağrılabilir
    targeted_tickers=None,          # None = tam evren
    not_run_policy="OVERWRITE",
)
```

```bash
make migrate              # sql/027-030 dahil
make audit-total-rasyo    # 15.000 senaryo
```
