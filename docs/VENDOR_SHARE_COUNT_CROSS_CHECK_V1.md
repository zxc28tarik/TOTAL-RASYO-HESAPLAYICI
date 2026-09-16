# Vendor pay-sayısı çapraz doğrulaması (StockAnalysis/S&P Global) — keşif notu

Tarih: 2026-09-15. Çalışma dalı: `claude/inspiring-cannon-ecxilb`. Kapsam:
**yalnızca araştırma/rapor** — production kodu, W7-A kanıt-tarihi politikası,
veto, ağırlık veya skorlama davranışı **hiç değiştirilmedi**.

## 1. Amaç ve sınır

Yeni bir potansiyel kaynak (`stockanalysis.com`, "Financial data is provided
by S&P Global Market Intelligence") test edildi — **W7-A/W7-B/W7-C'nin
period-native KAP/SPK kanıt zincirinin yerine değil, ona ek, bağımsız bir
çapraz doğrulama/anomali-tespit katmanı olarak.** Bu doküman yalnızca
bulguları kaydediyor; hiçbir üretim yolu bu kaynağı kullanmıyor ve
kullanmayacak (aşağıdaki §4'e bakın).

## 2. Yöntem

`stockanalysis.com/quote/ist/{TICKER}/financials/balance-sheet/` sayfasından
`Filing Date Shares Outstanding` / `Total Common Shares Outstanding`
satırları, dört ticker için (CCOLA, ZOREN, THYAO doğrudan bu oturumda;
BIMAS kullanıcının kendi önceki araştırmasından) alınıp
`kap_share_class_history_v1`'in ham `nominalValueOfShares`/
`nominalValuePerShare` metniyle karşılaştırıldı.

## 3. Bulgular

### CCOLA — vendor serisi geriye-dönük restate edilmiş

| Kaynak | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|
| StockAnalysis/S\&P | 2.798B | 2.798B | 2.798B | 2.798B | 2.798B |
| KAP (ödenmiş sermaye, TL) | — | — | 254,371,000 | 2,798,079,000 | — |

KAP'ın kendi finansallarında 2023→2024 arasında **gerçek** bir sermaye
artışı var (254,4m → 2.798,1m TL). Vendor serisi bu artışı **tüm geçmişe
geriye doğru uygulamış** — yani "as originally reported point-in-time"
değil, **restate edilmiş/normalize edilmiş** bir seri. Bu proje için önemi:
CCOLA'nın W7-C'de kullanılan çapası **2019-05-14** tarihli ve **2023-07-31**
cutoff'u için — bu artış (2024) cutoff'tan **sonra**, dolayısıyla W7-C'nin
sonucunu etkilemiyor; ama vendor verisinin doğrudan "geçmiş pay sayısı"
diye kullanılamayacağını doğruluyor.

### ZOREN — vendor "anomali"si aslında gerçek bir olay: nominal değer birleştirmesi

| Kaynak | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|
| StockAnalysis/S\&P | 2.5B | 2.5B | 5.0B | 5.0B | 5.0B |
| `kap_share_class_history_v1` (bu projenin ham-metin okuması) | 250B (13/09/2021) | 250B | **500B** (15/06/2023, W7-B'nin çapası) | — | **5B** (24/07/2025) |

İlk bakışta 100x'lik bir fark var gibi görünüyor — ama ham KAP metnini
doğrudan karşılaştırınca **kök sebep tam olarak belirlendi, bir hata değil,
gerçek bir kurumsal olay:**

```
15/06/2023: nominalValueOfShares(A+B)=5.000.000.000 TL, nominalValuePerShare="0,01"
            → pay sayısı = 5.000.000.000 / 0,01 = 500.000.000.000

24/07/2025: nominalValueOfShares(A+B)=5.000.000.000 TL (AYNI toplam sermaye),
            nominalValuePerShare="1,00"  ← DEĞİŞTİ
            → pay sayısı = 5.000.000.000 / 1,00 = 5.000.000.000
```

Yani ZOREN, 2023-06-15 ile 2025-07-24 arasında **nominal pay değerini 0,01
TL'den 1,00 TL'ye çıkarmış** (100:1 nominal değer birleştirmesi/"reverse
split" benzeri bir olay, toplam sermaye aynı kalırken pay sayısı 100'e
bölünüyor). StockAnalysis'in serisi bu **sonraki** durumu (5B) geriye doğru
tüm yıllara uygulamış — CCOLA'da görülenle **birebir aynı desen**.

**Bu bulgunun projeye önemi — doğrulama, tehdit değil:** W7-B'nin zaten
üretim-kabul edilmiş, CI'da geçen ZOREN çapası (2023-06-15,
**500.000.000.000 pay**, `docs/W7B_SPK_BULLETIN_EVIDENCE.md` §3'te zaten
"nominal pay değeri 1 TL değil 0,01 TL" notuyla kayıtlı) bu bağımsız
çapraz-kontrolle **doğrulandı, çürütülmedi**. Vendor'un "farklı" göstermesi,
2023-06-15'te vendor'un henüz gerçekleşmemiş 2025 sonrası nominal-değer
değişikliğini geriye uygulamasından kaynaklanıyor.

### THYAO — temiz eşleşme, anomali yok

StockAnalysis 2021-2026 boyunca sabit 1.380B; KAP'ın güncel çıkarılmış
sermayesi 1.380.000.000 TL (nominal 1 TL) ile birebir tutarlı. Bu dönemde
gerçek bir sermaye olayı yok — iki kaynak da aynı gerçeği yansıtıyor.

### BIMAS — farklı bir sınıf uyarı: treasury/net semantik farkı (kullanıcının bulgusu)

KAP: 2025/12 ödenmiş sermaye 600m TL → Mayıs 2026'da %100 bedelsiz artırım
→ 1.2B TL. Vendor ("Total Common Shares Outstanding") ise 2021→2026 boyunca
**hafifçe azalan** 1.198B→1.186B gösteriyor — KAP'ın **çıkarılmış/issued**
sermayesiyle aynı semantik değil. Muhtemel açıklama: vendor alanı **treasury
shares (kendi payı geri alımları) netlenmiş** bir "outstanding" rakamı
olabilir, `issued capital` değil. Bu CCOLA/ZOREN'deki "restate" sorunundan
**ayrı bir veri-kalitesi sınıfı**: aynı isim (`shares outstanding`) farklı
finansal kavramları örtüyor olabilir.

## 4. Politika sonucu (kullanıcının DO/DON'T çerçevesiyle birebir)

**YAPILMAYACAK:**
- StockAnalysis/Morningstar değeri hiçbir zaman canonical historical
  share-state olarak ingest edilmeyecek.
- Vendor serisi `kap_share_class_history_v1`'in ham
  `nominalValueOfShares`/`nominalValuePerShare` türetmesinin yerine
  **geçmeyecek**.
- Vendor'un geçmişte "aynı değeri gösterdiği" tek başına continuity
  (aradaki dönemde olay olmadığı) kanıtı **sayılmayacak** — CCOLA ve ZOREN
  ikisi de bunun neden yanlış olacağını doğrudan gösteriyor.
- W7-A'nın negatif-kanıt standardı (bugün sorgulanan kaynak, geçmişte "olay
  olmadığını" kanıtlayamaz) **değişmedi**.

**YAPILACAK (ileride, ayrı bir iş paketi olarak — bu dokümanın kapsamı
dışında, henüz uygulanmadı):**
1. Vendor kaynağı kendi provenance sınıfında (`INDEPENDENT_VENDOR_CROSS_CHECK`
   gibi) tutulmalı, asla `KAP_RAW_DERIVED` ile karıştırılmamalı.
2. `issued/capital-representing shares` vs `total common shares outstanding`
   vs `filing-date shares outstanding` vs `treasury shares` vs
   `weighted-average basic/diluted shares` ayrımı açıkça etiketlenmeli.
3. Vendor değeri KAP ham-türetmeyle uyuşursa confidence sinyali;
   uyuşmazsa **otomatik ret değil**, `REQUIRES_REVIEW` üretilmeli (tam
   olarak ZOREN'de olduğu gibi — "uyuşmuyor" ilk bakışta hata gibi
   görünüyordu, incelenince gerçek bir olay çıktı).
4. Vendor serisinin restated/split-adjusted olup olmadığı provenance'a
   yazılmalı.
5. Canonical PIT continuity hâlâ yalnızca period-native KAP/SPK kanıtıyla
   kurulmalı — bu doküman bunu değiştirmiyor.

## 5. Sıradaki (henüz yapılmadı)

- Morningstar Key Metrics sayfasının `As Originally Reported` modu CCOLA,
  ZOREN, THYAO, BIMAS, DEVA, JANTS için test edilmeli — bu mod gerçekten
  farklı (restate edilmemiş) geçmiş değerler döndürüyor mu, yoksa o da
  aynı geriye-dönük normalize deseni mi taşıyor, belirlenmeli.
- DEVA ve JANTS için aynı CCOLA/ZOREN tipi çapraz kontrol yapılmadı.
- Bu bulgular, istenirse, W7-B/W7-C'nin izlediği tam denetim şablonuna
  (audit script + hedef testler + mutasyon + `--check`) dönüştürülebilir —
  ama bu, kullanıcının "önce yalnızca audit yap ve raporla" talimatına göre
  **ayrı, sonraki bir karar**.

## 6. Değişmeyenler

`src/`, `sql/`, `config/` hiç değişmedi. W7-A'nın kanıt-tarihi politikası,
W7-B/W7-C'nin çapa çözümleme yöntemi, minimum_peer_count/coverage_weight
eşikleri, model ağırlıkları **hiç dokunulmadı**. W7-B'nin ZOREN çapası
(500.000.000.000, 2023-06-15) bu bağımsız kaynakla **çelişmiyor** —
tam tersine, ZOREN'in 2023-06-15 sonrası gerçekleşen nominal-değer
birleştirmesi keşfedilerek doğrulandı.
