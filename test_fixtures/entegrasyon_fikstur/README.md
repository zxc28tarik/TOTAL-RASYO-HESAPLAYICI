# Entegrasyon Test Fikstürü — Point-in-Time Doğrulama

Entegrasyon paketi geldiğinde koşturacağım fikstür ve **elle doğrulanmış referans değerler**. Canlı PostgreSQL 16'da üretildi.

## Tasarım

Tek bankada (`FIXBNK`) beş tuzak aynı anda:

| Dönem | Tuzak |
|---|---|
| 2024-Q3 | **Tamamen eksik** → yuva `None` kalmalı (sıkıştırma testi) |
| 2025-Q1 | İki sürüm, **farklı** `published_at` → point-in-time seçim testi |
| 2025-Q2 | İki sürüm, **aynı** `published_at` → tie-break testi |
| 2023-Q3/Q4 | Pencere **dışında** → "son 8 kayıt" hatası testi |
| — | Sıralama → ters sıra testi |

## Referans değerler

### `analysis_date = 2026-03-01`, hedef dönem 2025-Q4

Doğru seri (2024-Q1 … 2025-Q4):
```
[0.1560, 0.1898, None, 0.2346, 0.2100, 0.2400, 0.2950, 0.3080]
```

2025-Q1 `RESTATED` (2025-11-20 yayımlandı, analiz tarihinden önce); 2025-Q2 `RESTATED` (tie-break ile).

| | `trend_slope` | `sd_roe_effective` | `n_valid` | `roe_missing` | `roe_sus` |
|---|---|---|---|---|---|
| **DOĞRU** | **+0,021040** | **0,01192010** | **7** | **1** | **0,234600** |
| (a) sıkıştırılmış | +0,019000 | 0,02194248 | 11 | 0 | 0,234600 |
| (b) "son 8 kayıt" | +0,012617 | 0,02603199 | 8 | 0 | 0,254450 |
| (c) tie-break yok (karma) | +0,025179 | 0,01140543 | 9 | 0 | 0,210000 |
| **(c') tie-break yok (izole)** | **+0,021714** | **0,01128894** | **7** | **1** | **0,234600** |
| (d) ters sıralama | **−0,021040** | 0,01192010 | 7 | 1 | 0,234600 |

**(c) ile (c') farkı önemli.** `sorgu_yanlis_ornekleri.sql`'deki (c) yalnız tie-break'i değil takvim yuvasını, sekiz dönem penceresini ve `LEFT JOIN`'i de bozuyor — sonucu yanlış ama sebebi izole değil. `sorgu_tiebreak_izole.sql` **yalnız `ORDER BY`'da** farklı; test kırılırsa sebebin kesinlikle tie-break olduğu bilinir.

(c') senaryosunda tek fark 2025-Q2'de: `RESTATED 0,2400` yerine `ORIGINAL 0,2809`. `n_valid`, `roe_missing_count` ve `roe_sus` **aynı kalıyor** — bu yüzden izole tie-break hatası ancak `slope` + `sd_eff` + `selected_version_tag` birlikte kontrol edilirse yakalanır.

Ama (c') değerlerinin *ortaya çıkması* garanti değil: tie-break'siz sorgu tesadüfen doğru kaydı da seçebilir. Test bu yüzden "fark ya yok ya da yalnız 2025-Q2'de" şeklinde yazıldı; kararlı şart doğru sorgunun **her zaman** `RESTATED` seçmesi.

### `analysis_date = 2025-10-01` (point-in-time kontrolü)

```
[0.1560, 0.1898, None, 0.2346, 0.2689, 0.2400, None, None]
```

2025-Q1 artık `ORIGINAL` (0,2689) — `RESTATED` henüz yayımlanmamış. 2025-Q3/Q4 de yayımlanmamış.

| `trend_slope` | `sd_roe_effective` | `n_valid` | `roe_missing` |
|---|---|---|---|
| +0,024300 | 0,00845082 | 5 | 3 |

**Aynı banka, aynı sorgu, farklı analiz tarihi → farklı seri.** Bu tablo point-in-time'ın çalıştığını gösterir; sabit çıkıyorsa geçmişe gelecek verisi sızıyor demektir.

## Önemli: tek metrik yetmez

`trend_slope` tek başına **(c) tie-break kaybını** yakalayamaz — ilk tasarımımda aynı eğimi veren bir varyant vardı. Üç metrik birlikte kontrol edilmeli:

- `trend_slope` → (b), (d) yakalar
- `sd_roe_effective` → (a), (b), (c) yakalar
- `roe_sus` → (b), (c) yakalar
- `roe_missing_count` → (a), (b), (c) yakalar (doğru sorguda **1**, hepsinde **0**)

En basit tek gösterge `roe_missing_count`: doğru sorguda 1, üç yanlış sorguda da 0. Eksik çeyreğin **görünmesi** doğru davranışın imzası.

## Dosyalar

- `fixture_pit_bank.sql` — tablo + veri. `version_sequence` alanı tie-break için zorunlu.
- `fixture_pit_bank_orders.sql` — **fiziksel sıra bağımsızlığı testi**: aynı mantıksal veri, iki farklı ekleme sırasıyla (A: ORIGINAL önce, B: RESTATED önce).
- `sorgu_dogru.sql` — takvim yuvası (`generate_series`) + `DISTINCT ON` + üçlü tie-break + `LEFT JOIN` ile eksik koruması.
- `sorgu_tiebreak_izole.sql` — doğru sorguyla **tek farkı** `ORDER BY`'da tie-break olmaması.
- `sorgu_yanlis_sikistirma.sql` / `sorgu_yanlis_son_8_kayit.sql` / `sorgu_yanlis_tiebreak_karma.sql` — üç yanlış desen, **ayrı dosyalarda** (tek dosyada birleşikken yakalama oranları yeniden üretilemiyordu).
- `fixture_intraday_timestamptz.sql` — gün içi look-ahead fikstürü (`timestamptz` riski).
- `test_fixture_reference.py` — otomatik kabul testi (**20 test**).

## Fiziksel sıra bağımsızlığı — canlı doğrulandı

Tek tabloyla kanıtlanamaz. (İlk sürümde `CLUSTER ... USING pkey` ile "tabloyu karıştır" yazmıştım — yanlıştı; `bigserial` pkey tabloyu ekleme sırasına **dizer**, yani tie-break'siz sorgunun seçimini daha da sabitler.) Doğru yöntem iki kurulum:

| Kurulum | tie-break VAR | tie-break YOK (bir koşuda gözlenen) |
|---|---|---|
| A (ORIGINAL önce) | `RESTATED 0,2400` | `ORIGINAL 0,2809` |
| B (RESTATED önce) | `RESTATED 0,2400` | `RESTATED 0,2400` |

Sağ sütun **tekrarlanabilir değildir** — ana fikstürü yeniden kurduktan sonra tie-break'siz sorgunun `RESTATED` seçtiği, yani doğru sorguyla aynı sonucu verdiği gözlendi. Tanımsız davranışın canlı örneği.

Doğru sorgu iki kurulumda da aynı; tie-break'siz sorgu **fiziksel ekleme sırasına göre değişiyor**.

⚠️ **CI şartı yalnız üst satır olmalı.** Tie-break'siz sorgunun sonucu tanımsızdır — başka plan, indeks veya PostgreSQL yapılandırmasında A ve B tesadüfen aynı kaydı seçebilir. Kararlı kabul testi:

```
doğru sorgu / Kurulum A  →  RESTATED 0,2400
doğru sorgu / Kurulum B  →  RESTATED 0,2400
```

Alt satır (tie-break'siz sorgunun farklılaşması) README'de **hata kanıtı** olarak kalır, zorunlu CI beklentisi değil.

## Üretimde `timestamptz` gerekli

Fikstür `published_at date` kullanıyor; günlük analiz için yeterli. Ama hedef sistem gün içinde veya anlık değerlendirme yapacaksa üretim tablosu şunu korumalı:

```sql
published_at  timestamptz
analysis_at   timestamptz
```

Aksi halde aynı gün 10:00 ve 17:00'de yayımlanan iki kayıt aynı tarih sayılır; **12:00'de yapılan tarihsel analizde 17:00 verisi geçmişe sızar**. `version_sequence` hangi sürümün seçileceğini çözer ama verinin o saatte bilinip bilinmediğini çözemez — bu ayrı bir look-ahead kanalı.

`fixture_intraday_timestamptz.sql` bunu canlı gösteriyor. 2025-06-30 dönemi için `ORIGINAL` 10:00'da, `RESTATED` 17:00'de yayımlanmış:

| Analiz saati | `published_at <= analysis_at` (doğru) | `published_at::date <= analysis_at::date` (yanlış) |
|---|---|---|
| 09:00 | *(yok)* | `RESTATED 0,2400` ← **sızıntı** |
| 12:00 | `ORIGINAL 0,2809` | `RESTATED 0,2400` ← **sızıntı** |
| 18:00 | `RESTATED 0,2400` | `RESTATED 0,2400` |

09:00 satırı özellikle kötü: henüz hiçbir şey yayımlanmamışken sistem yedi saat sonra gelecek veriyi kullanıyor.

**Bu artık otomatik test ediliyor** (`test_intraday_point_in_time`, `test_intraday_tarihe_indirgeme_sizdirir`). Önceki sürümde fikstür pakette vardı ama hiçbir assertion yoktu — yani "14 test geçti" sonucu `timestamptz` güvenliğini kanıtlamıyordu.

Üretim sorgusu gün içi/anlık hedef için `published_at <= :analysis_at::timestamptz` kullanmalı; `::date` indirgemesi look-ahead açar.

⚠️ `test_fikstur_semasi_timestamptz` **üretim sorgusunu denetlemez** — yalnız fikstür tablosunun şemasını kontrol eder. Entegrasyonda gün içi senaryolar üretim sorgusundan geçirilmeli; SQL metninde "timestamptz" kelimesi aramak yeterli değildir.

## Otomatik kabul testi

`test_fixture_reference.py` bu referansları PostgreSQL'e karşı otomatik doğrular. Entegrasyon paketi geldiğinde `SORGU_DOSYASI` sabiti üretim sorgusuna çevrilir; beklenen değerler aynı kalır.

```bash
export PGHOST=localhost PGUSER=postgres PGPASSWORD=postgres PGDATABASE=postgres
pytest test_fixture_reference.py -q
```

Karşılaştırılan alanlar: `selected_version_tag`, `selected_published_at`, `quarter_slots`, `roe_series_canonical`, `trend_slope`, `sd_roe_effective`, `n_valid`, `roe_missing_count`, `roe_sus`.

**SQL hatası sert kırılır.** `_psql()` içinde `psql -X -v ON_ERROR_STOP=1` kullanılıyor ve hata durumunda `pytest.fail` çağrılıyor — `skip` **değil**. Bir kabul testinin en tehlikeli hali, üretim sorgusu bozukken sessizce atlanmasıdır. `psql` başlatılamazsa `OSError` yakalanıp `fail` ediliyor — buna `FileNotFoundError` (program yok) ve `PermissionError` (çalıştırılamıyor) da dahil.

Bu davranış eklendiğinde fikstürün kendisindeki gizli bir hata anında ortaya çıktı: `LIKE ... INCLUDING ALL` sıra testi tablolarına ana tablonun sequence'ini paylaştırıyordu ve yeniden kurulum `cannot drop table` ile patlıyordu — eskiden bu sessizce atlanıyordu.

### Yakalama gücü (20 testle yeniden ölçüldü)

`SORGU_DOSYASI` yanlış bir sorguya çevrildiğinde:

| Yanlış desen | Kırılan |
|---|---|
| Sözdizimi hatası / bağlantı yok / `psql` yok | **hepsi** (sert kırılma) |
| Motor import edilemiyor | **collection error** (skip değil) |
| Sıkıştırma (eksik atılmış) | 12 / 20 |
| "son 8 kayıt" (pencere kayması) | 12 / 20 |
| Karma tie-break | 12 / 20 |
| İzole tie-break | 9 / 20 |

Geçen 8 test gün içi ve fiziksel sıra testleri — `SORGU_DOSYASI`'ndan bağımsız oldukları için etkilenmiyorlar.

## Kullanım

```bash
psql -f fixture_pit_bank.sql
psql -v ticker="'FIXBNK'" -v analysis_date="'2026-03-01'" \
     -v son_donem="'2025-12-31'" -f sorgu_dogru.sql
```

Entegrasyon paketinin sorgusu bu fikstürde yukarıdaki referans değerleri üretmeli. Üretmiyorsa fark hangi metrikte çıkıyorsa hata da orada.
