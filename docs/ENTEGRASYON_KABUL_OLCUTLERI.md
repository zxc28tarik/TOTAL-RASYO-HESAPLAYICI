# Üretim Entegrasyonu — Kabul Ölçütleri

Saf fonksiyon fazı kapandı (277 passed, 1 xfailed). Bu belge entegrasyon paketi geldiğinde canlı PostgreSQL 16'da koşturacağım doğrulamaları önceden tanımlar.

**Neden önceden:** Bundan sonraki hata sınıfı motorun göremeyeceği türden — yanlış rapor sürümü, yanlış dönem sırası, geçmiş değerlemeye sızan gelecek verisi. Bunların hepsi **tip olarak kusursuz** girdi üretir; hiçbir giriş kapısı yakalayamaz ve sonuç tamamen makul görünür. Bu yüzden testler motor tarafında değil, sorgu tarafında olmak zorunda.

---

## 1. `_to_canonical_row()` — sabit zaman yuvası

Sorgu son sekiz **gerçek çeyreğin** yuvalarını üretmeli, eksikler `None` olarak korunmalı.

**Yanlış** (motor düzelmiş olsa bile hatayı geri getirir, testler yakalamaz):
```python
roe_series = [row.roe for row in rows if row.roe is not None]
```

**Doğru:**
```python
roe_series = [roe_2024q1, None, roe_2024q3, ...]   # 8 yuva, eksikler None
```

### Doğrulamam

Eksik çeyreği olan bir bankaya gerçek sorguyu koşturup elle kurulmuş referansla karşılaştıracağım:

| Seri | Beklenen `trend_slope` |
|---|---|
| `[0.12, None, 0.20, None, 0.28, 0.32]` | **0,040000** |
| Sıkıştırılmış hali (hata) | 0,073333 |

Fark %83 — sıkıştırma varsa hemen görünür.

**Kabul:** `trend_slope` referansla `1e-9` hassasiyetinde eşit; `roe_missing_count` gerçek eksik sayısına eşit.

## 2. Point-in-time sürüm seçimi (`ORIGINAL` / `RESTATED`)

### 2a. Eşitlik bozma kuralı — canlı olarak doğrulandı

Aynı `period_end` için iki kaydın `published_at` değeri eşitse, tie-break olmadan sonuç **fiziksel satır sırasına** bağlı kalıyor. PostgreSQL 16'da gösterdim:

| Sorgu | Seçilen |
|---|---|
| `ORDER BY published_at DESC LIMIT 1`, ORIGINAL önce eklenmiş | `ORIGINAL`, `roe = 0,28` |
| Aynı sorgu, RESTATED önce eklenmiş | `RESTATED`, `roe = 0,19` |
| `ORDER BY published_at DESC, version_tag DESC, id DESC` | her planda `RESTATED` |

Aynı veri, aynı sorgu, **%47 farklı ROE** — yalnız satır sırası yüzünden. Bu fark `ROE_sus`'a, oradan `V_mid`'e geçer ve hiçbir kapı yakalamaz.

**Zorunlu kural:**

```sql
ORDER BY published_at DESC, version_sequence DESC, record_id DESC
```

Veri modelinde `version_sequence` yoksa eklenmeli. Sorgu sonucu veritabanının tesadüfi satır sırasına bağlı kalmamalı.

### 2b. Sekiz çeyreğin başlangıç noktası

Yuvalar **"veritabanındaki son sekiz kayıt"** üzerinden değil, hedef rapor dönemine bağlı **takvim çeyrekleri** üzerinden kurulmalı. Hedef `2025-Q4` ise yuvalar `2024-Q1 … 2025-Q4`.

Neden kritik: bir bankada iki çeyrek eksikse, "son sekiz kayıt" pencereyi sessizce iki çeyrek geriye uzatır. Seri yine sekiz elemanlıdır, hiçbir eksik görünmez, `roe_missing_count = 0` çıkar — ama ölçülen dönem yanlıştır ve trend farklı bir zaman aralığından hesaplanır.


Aynı `period_end` için birden fazla `version_tag` varsa, `analysis_date`'te **bilinen** sürüm seçilmeli.

### Doğrulamam

Bir bankaya her dönem için `RESTATED` sürüm ekleyip (v3.2 doğrulamasında yaptığım gibi) iki şeyi kontrol edeceğim:

- `n_eval` **artmamalı** — yeniden açıklama ikinci çeyrek sayılmıyor
- Geçmiş bir `asof` ile koşulduğunda `RESTATED` yayın tarihinden önceyse **kullanılmamalı**

**Kabul:** `n_eval` sabit; `asof < restated_published_at` durumunda `ORIGINAL` değerleri kullanılıyor.

## 2c. İzlenebilirlik — ara ürünler saklanmalı

Yalnız `V_mid` kontrol edilirse hata nerede olursa olsun aynı görünür. Şu alanlar kayıtta bulunmalı ki hatanın sorguda mı, dönüşümde mi, sektör dağılımında mı, güven zincirinde mi olduğu doğrudan anlaşılsın:

```
selected_version_tag      selected_published_at     quarter_slots
roe_series_canonical      roe_missing_count         trend_slope
sector_sample_size        sector_asof_cutoff        sd_roe_floor
floor_source              sd_roe_effective          payout_factor
outlier_conf_penalty      corner_conf_penalty       v_conf
```

## 3. Sektör artık dağılımının point-in-time kurulması

`sector_residual_scales` bugünkü tüm bankalardan değil, `analysis_date`'te bilinen finansallardan kurulmalı.

Hedef bankanın kendi gözleminin sektör quantile hesabına **katılıp katılmayacağı** açıkça belirlenmeli ve test edilmeli (leave-one-out mu, değil mi).

**Kabul:** Geçmiş bir `asof` ile koşulduğunda dağılıma o tarihte henüz raporlamamış banka girmiyor; `floor_source`, `sd_roe_floor` ve `sector_sample_size` tarihe göre değişiyor.

## 4. İki adımlı motor çağrısı

Geçici sarmalayıcı (`bank_valuation_with_estimated_uncertainty`) **kullanılmamalı**:

```python
u = estimate_roe_uncertainty(roe_series, sector_residual_scales=...)
r = bank_valuation(..., sd_roe=u["sd_roe_effective"],
                   band_width_shadow_mode=...)
```

**Kabul:** Üretim kodunda sarmalayıcı çağrısı yok; entegrasyon bittiğinde sarmalayıcı kaldırılıyor.

## 5. Dört çarpanlı güven zinciri

```
v_conf = tier_cap × payout_faktörü × u["conf_penalty"] × r.get("corner_conf_penalty", 1.0)
```

Referans uygulamada doğrulanmış değerler (`test_33`, `test_70`):

| Durum | `v_conf` |
|---|---|
| payout var, uç değer yok, tam köşe | **0,800** |
| payout yok | **0,560** = 0,80 × 0,70 |
| uç değer var | **0,680** = 0,80 × 0,85 |
| payout yok + uç değer | **0,476** = 0,80 × 0,70 × 0,85 |
| + kısmi köşe kaybı | × 0,70 daha |

**Kabul:** Dört senaryo canlı veritabanında birebir çıkıyor **ve** `valuation_band_periods.confidence_factors` alanına yazılıyor. `conf_penalty` yalnız `result["uncertainty"]` içinde dönüyor — üretim hattı uygulamazsa `outlier_flag` ışıksız bayrak kalır.

## 6. Gölge modda band genişliği dağılımı

`band_width_shadow_mode=True` ile koşulup üç eşikle reddedilecek oran ölçülmeli:

| Eşik | Reddedilecek oran |
|---|---|
| `max_halfwidth = 0,80` | ? |
| `0,90` | ? |
| `1,00` | ? |

Sektör × dönem × şirket türü bazında. `0,80` yaklaşık 5x'ten (`exp(2×0,80) ≈ 4,95`) geniş bandları reddediyor.

**Kabul:** Rapor üretiliyor; sert kapı **bu veriye bakılmadan** açılmıyor.

## 7. Kalibrasyon için gereken diğer dağılımlar

Hepsi sektör × dönem bazında:

- `floor_binding_count / valuation_usable_count` — taban istisna mı, varsayılan mı?
- `justified_pb` dağılımı
- `z_val` dağılımı ve `s_val ∈ {0, 1}` doyma oranı
- `outlier_flag` oranı
- `roe_sus`, `coe`, `rf` dağılımları
- Fiyatın `V_mid` katı

**Önemli:** `rf = %30` iken `COE ≈ %37`. ROE'si bunun altında kalan bankalarda `justified_pb < 1` ve piyasa 1x defter civarında fiyatlıyorsa `s_val` toplu halde 0'a doyabilir. Doyma yaygınsa **önce** nominal/reel tutarlılık ve faiz rejimi kontrol edilmeli; `Z_CAP_VAL` en son düşünülmeli.

## 8. Kanonik dönüşüm katmanı

PostgreSQL → pandas → motor hattında tipler `float`/`None`'a çevrilmeli. Motor kapıları (`pd.NA`, `np.bool_`, `OverflowError`) yerinde kalmalı — saf fonksiyon başka yerden de çağrılabilir — ama asıl koruma dönüşümde olmalı.

**Kabul:** `_to_canonical_row()` çıktısı yalnız `float` ve `None` içeriyor; motor kapıları hiç tetiklenmiyor (tetiklenirse dönüşümde eksik var demektir).

---

## Regresyon değişmezleri

Entegrasyon bunları **değiştirmemeli**:

```
BNK1:  V_mid = 6,8934456
       band  = 5,2707 – 10,1128  (1,9187x)
       sd_roe_effective = 0,01015581
       n_total = 27, n_unique = 18
```

Ayrıca v3.1/v3.2 düzeltmeleri yerinde kalmalı: NaN propagasyonu, sütun ağırlıkları, kapsam daraltması, bölünme sözleşmesi (10:1 testinde `follow_score = 0,512`).

## Kalibrasyon yapılmadan değiştirilmeyecekler

`Z_CAP_VAL`, `absolute_floor`, `max_halfwidth = 0,80` sert kapısı, uç değer eşikleri, güven cezası katsayıları, `dof_correction`, AL/İZLE/UZAK eşikleri.

Önce gerçek dağılım raporu, sonra ayrı model kararı.

## Kapsam dışı (teyit)

Enflasyon/TMS 29 (kullanıcı tarafında), kur modülü (ayrı iş paketi), ağırlık optimizasyonu ve AL eşiği (yol haritası 7. madde).
