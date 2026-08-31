# Total Rasyo Hesaplayıcı — Güncel Proje Durumu

Son doğrulama: **2026-08-31**

Bu belge, sohbet sayfaları değişse bile projenin hedefini ve son doğrulanmış durumunu kaybetmemek için tek başlangıç noktasıdır.

## Tek kanonik GitHub deposu

Depo: [`zxc28tarik/TOTAL-RASYO-HESAPLAYICI`](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI)

| Hat | Dal | Doğrulanmış baş | Anlamı |
|---|---|---|---|
| Üretim | `main` | `84494e29824809b20b5410c8b160ef38f70c27c9` | V24-F üretim fotoğrafı; deneysel tarihsel veri çalışması buraya henüz terfi ettirilmedi |
| Aktif geliştirme | `v24-real-data-work` | `883e680a2564e38f4c08a21bc88aa95b8f164036` | PR #20 bağımsız denetimden temiz geçti, `816393bbfe428d17cbadbc0ca553a7457795713f` ile birleşti; V24 Real Data CI #62 yeşil tamamlandı ve makine-okur `pit_total_rasyo_replay` kanıtı bu evidence commit'inde kalıcılaştırıldı |

`v24-real-data-work`, V24-F ortak atasından sonra aktif geliştirme dalıdır. `main` üzerindeki sonraki commitler üretim CI/kanıt belgeleridir. Bu nedenle tek depo hedefi, iki dalı körlemesine ezmek değil; geliştirme tamamlanıp bütün kapılar geçtiğinde kontrollü terfi yapmaktır.

ChatGPT içindeki eski ZIP ve bundle dosyaları geri dönüş/arşiv amacı taşır. Yüklenme tarihleri sürüm sırasını göstermez; GitHub dal/commit geçmişi ve başarılı CI koşusu sürüm otoritesidir.

## Şu anki hedef

Yakın hedef, **2021-08 ile 2026-07 arasındaki 60 ay için BIST100/XU100 üzerinde savunulabilir point-in-time 5 yıllık backtest** üretmektir.

Her ay:

- o tarihte gerçekten BIST100 üyesi olan hisseler kullanılacak;
- yalnız o anda bilinebilen finansal veri ve fiyatlar görülecek;
- güncel durum sızıntısı, sonradan düzeltilmiş veri yanlılığı, survivorship bias ve sessiz fallback olmayacak;
- o ay geçerli net asgari ücretin 2 katı portföye eklenecek;
- en fazla 6 hisse tutulacak; alış, satış, elde tutma, nakit ve NAV kaydedilecek;
- sonuç XU100 ile karşılaştırılacak.

Uzun vadeli ürün hedefi, aynı sektör motorlarını bütün BIST hisseleri için otomatik resmi veri akışıyla çalıştırıp Total Rasyo sıralaması üretmektir. Ancak mevcut teslimat sırası önce 60 aylık tarihsel doğrulamayı kapatmaktır.

## Kapanan işler

| Alan | Durum | Kanıt/ölçü |
|---|---|---|
| 60 aylık XU100 sinyal takvimi | **KAPALI** | 2021-08-02 .. 2026-07-01, 60/60 |
| Tarihsel BIST100 evreni | **KAPALI** | 21 periyodik grup, 180 değişim çifti, 114 periyodik olmayan duyuru denetimi |
| Periyodik olmayan üyelik olayı | **KAPALI** | 2026-06-18 KONTR çıkış / BERA giriş |
| Ticker soy zinciri | **KAPALI** | 38 resmi kod değişikliği |
| Aylık üye işlem fiyatı | **KAPALI** | 6000/6000; 12 Yahoo boşluğu resmi Borsa THB ile tamamlandı |
| Asgari ücret kaynağı | **KAYNAK KİLİTLİ** | `WAGE_TR_NET_CSGB_2021_2026_V1`; katkı = 2 × geçerli net ücret |
| Kurumsal aksiyon motoru | **SEMANTİK KAPALI** | bölünme/bedelsiz, temettü nakdi, kod değişimi ve olay sırası |
| PIT CORE+VAL, RSC, M1 | **KAPALI** | DB-free ve cutoff-sonrası veri reddi |
| PIT M2 — altı sektör ailesi | **KAPALI** | NONFIN, HOLDING, GYO, INSURANCE, FINANCIAL, BANK |
| PIT M3 replay motoru | **KAPALI** | PR #13 iki bağımsız denetimden geçti; DB-free tarihsel yol canlı beta davranışından izole; pandas 2.2.3 uyumluluğu ayrı CI kapısı |
| Gerçek 60 aylık M3 kaynak paketi | **KAPALI** | PR #15 birleşti; 209 ticker/210 rota, 5×1.483 resmî endeks kapanışı, 7 doğrudan ham kaynak, SHA256 kilidi ve deterministik yeniden üretim doğrulandı |
| PIT Ek4 replay | **KAPALI** | PR #16 birleşti; DB-free, 20 işlem aralığı, ortak canlı formül, tarih-doğru M3 sektör rotası, ayrı piyasa kesimi ve XU100 fallback yasağı testlerle kilitli |
| PIT Ek1 + `good_count_ge8` replay | **KAPALI** | PR #17 birleşti; DB-free, PIT M1 ile aynı son RSC dönemi, ortak canlı formül, eksik-count fallback yasağı ve gerçek üretim veto sınırı 8/8 mutasyonla doğrulandı |
| PIT Ek9 replay | **KAPALI** | PR #18 bağımsız gerçek-diff ve mutasyon denetiminden blocker/major olmadan geçti; DB-free, 63 günlük getiri std (`ddof=1`), 0.06 volatilite cap'i, tam 64 fiyat pozisyonu, `pct_change(fill_method=None)`, cutoff-sonrası veri reddi ve XU100 fiyat fallback yasağı kilitli |
| 60-cutoff birleşik Total Rasyo replay/sıralama | **KAPALI** | PR #19 bağımsız gerçek-diff + 12 mutasyon denetiminden temiz geçti ve `f39c6e4b69bd66d13192d8c377eb8f0a76aafdff` ile birleşti. PR #20 evidence/CI boşluğunu üretim/replay koduna dokunmadan kapattı; merge `816393bbfe428d17cbadbc0ca553a7457795713f`, V24 Real Data CI #62 SUCCESS, kalıcı evidence commit'i `883e680a2564e38f4c08a21bc88aa95b8f164036` |
| V24-G readiness katmanı | **UYGULAMA KAPALI** | report-only, fail-closed; gerçek veriyle `READY` henüz alınmadı |

## Aktif KAP toplu finansal kaynak çalışması

KAP'ın dönem bazlı toplu finansal tablo dışa aktarımı için fail-closed HTML
parser, exact-label semantik adapter ve tekrar başlatılabilir arşiv envanter
scripti geliştirme dalında hazırlanmıştır. `KAP_2021_3A.zip` pilot doğrulamasında
arşiv SHA256 değeri
`d953adceb72accfc4294cce4b40e79121ba10529ad2a17ef3f60e61654ecd654`
olarak kilitlenmiş ve 466 raporun tamamı sınıflandırılmıştır:

- 404 `NONFIN`/`HOLDING` raporu exact-label kurallarıyla eşleşti;
- 62 banka, katılım bankası, sigorta veya diğer farklı teknik şema
  `UNSUPPORTED_SCHEMA` olarak açıkça ayrıldı;
- teknik parser reddi kalmadı;
- tarama bir seferde yalnız bir raporu belleğe alıp her rapordan sonra
  checkpoint yazar;
- sektör yönlendirmesi henüz otoritatif değildir ve bu pilot tek başına gerçek
  60-cutoff veri hazırlığının tamamlandığı anlamına gelmez.

Ham KAP ZIP'i ve tekrar üretilebilir CSV çıktıları Git geçmişine alınmaz; kaynak
hash'i, parser, eşleştirme sözleşmesi ve doğrulama testleri sürümlenir.

Son otomatik kanıt commit'i [`883e680a`](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/commit/883e680a2564e38f4c08a21bc88aa95b8f164036)'dır. Bu commit, PR #20 merge commit'i [`816393bb`](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/commit/816393bbfe428d17cbadbc0ca553a7457795713f) üzerinde çalışan V24 Real Data CI #62 sonucunu kaydeder.

- geniş pandas 2.2.3 / numpy 1.26.4 uyumluluk kapısı: **191 geçti, 1 uyarı**;
- hedefli gerçek-veri sözleşmeleri: **329 geçti, 5 uyarı**;
- tam regresyon: **1798 geçti, 32 uyarı**;
- BANK v4.7: **277 geçti, 1 beklenen xfail, 1 uyarı**;
- schema migration: **PASS**;
- evidence generation: **PASS**;
- evidence persistence: **PASS**;
- iş akışı sonucu: **SUCCESS**.

Makine-okur kapanış da doğrulandı: `docs/V24_REAL_DATA_CI_EVIDENCE.json` içindeki `tested_commit_sha`, PR #20 merge SHA'sı `816393bbfe428d17cbadbc0ca553a7457795713f`'dir. Aynı dosyada `pit_total_rasyo_replay.result=PASS`, `veto_threshold=5`, `veto_factor=0.6`, tam 60 cutoff (`2021-08 .. 2026-07`), `score_rejection_coverage=EXHAUSTIVE` ve `real_cutoff_execution_clock_policy_authorized=false` kalıcı olarak kayıtlıdır. Böylece birleşik replay/sıralama katmanının formal evidence kapanışı tamamlanmıştır; gerçek işlem saati politikası ise bilinçli olarak açık kalır.

PR #13'ün canlı beta uyumluluğu, tarihsel `pct_change(fill_method=None)` davranışı ve pandas sürüm kapısı Claude tarafından gerçek diff ve mutasyon testleriyle bağımsız olarak denetlendi; blocker/major kalmadan birleştirildi.

PR #15'in gerçek M3 veri paketi iki bağımsız denetimden geçti. İkinci turda `GRTRK -> GRTHO` kimlik zinciri yol+SHA256 ve mutasyon testleriyle sertleştirildi; GitHub CI #41 ve merge-sonrası CI kanıtı yeşil tamamlandı.

PR #16'nın PIT Ek4 replay motoru bağımsız diff ve mutasyon denetiminden blocker/major olmadan geçti. GitHub PR CI #44; pinned pandas 2.2.3, şema, hedefli gerçek-veri sözleşmeleri, tam regresyon ve BANK v4.7 kapılarının tamamında yeşildir. Merge commit'i `b24208141cce48e6009943d514d04e9ef5e18693` olup PR head içeriğiyle bit-bit eşleşir.

PR #17'nin PIT Ek1/good-count replay motoru bağımsız diff ve sekiz mutasyon denetiminden blocker/major olmadan geçti. GitHub PR CI #47; pinned pandas 2.2.3'te 91, hedefli pakette 229, tam regresyonda 1.758 test ve BANK v4.7 kapılarının tamamında yeşildir. Merge commit'i `20b2c1f9afb5aa7c04a8a42fdf91384484d9a14d` olup denetlenen PR head ağacıyla bit-bit eşleşir.

PR #18'in PIT Ek9 replay motoru bağımsız gerçek diff denetiminden blocker/major olmadan geçti. Denetlenen head `d1fe827c1ff42b1b0ed63552f30124528b6fe6be`, merge commit'i `f3949402204e5a63a8072ec7925a66414774a15c` ile 9/9 dosyada bit-bit eşleşir. Canlı `run_daily_pipeline._compute_ek9_vol` veri hazırlığı değişmemiştir: SQL, `COALESCE(adj_close, close)`, pivot, varsayılan `pct_change()`, `< lookback+2` kapısı ve `tail(lookback)` korunur; yalnız `std(ddof=1) -> inf/NaN temizliği -> 0.06 cap` aritmetiği saf paylaşılan fonksiyona taşınmıştır. Tarihsel adapter tam 64 fiyat/63 getiri penceresinde `pct_change(fill_method=None)` kullanır, DB-free çalışır, cutoff-sonrası veri ve eksik fiyat için fail-closed davranır ve XU100 fiyat fallback'i kabul etmez. Bağımsız mutasyon turunda `ddof`, 0.06 cap, 63-lookback, cutoff-sonrası fiyat ve `fill_method=None` korumaları kırılmıştır. Merge-sonrası CI #51 ve `V24_REAL_DATA_CI_EVIDENCE.json` sonucu PASS'tir.

PR #19, kapalı altı modülün tarihsel sonuçlarını tek-cutoff bazında üretim Total Rasyo combiner'ına bağlar. `compute_total_rasyo` veya kapalı replay fonksiyonları değiştirilmedi. M2 altı mevcut sektör-family replay sonucundan gelir; diğer beş modül mevcut replay fonksiyonları çağrılarak üretilir. Bir modül bile eksik/rejected ise skor üretilmez. `good_count_ge8`, Ek1'in M1 ile aynı dönem lineage'ından taşınır; veto eşiği/faktörü üretim scorer'dan devralınır. Bağımsız denetimde 7 dosyalık gerçek diff doğrulandı, 13 kapalı üretim dosyasının değişmediği teyit edildi ve istenen 12 mutasyon noktasının tamamı kırıldı. PR head `e984db473a150adb9ba0766ad4df78c2115f428b`, merge commit'i `f39c6e4b69bd66d13192d8c377eb8f0a76aafdff`'dir.

PR #20 üretim/replay koduna dokunmadan yalnız geniş `V24 Real Data CI` tetikleme/test ve makine-okur evidence kapsamını tamamladı. Bağımsız denetimde gerçek diff'in yalnız üç dosya olduğu, üretim/replay dosyalarının değişmediği, push/PR tetikleyicilerinin ve pinned/targeted test kapılarının doğru genişletildiği, evidence generate/persist ayrımının doğru olduğu ve yeni JSON bloğunun gerçek sözleşmeyle tutarlı olduğu doğrulandı. Denetim `BLOCKER YOK, MAJOR YOK — TEMİZ` sonucu verdi. PR #20 `816393bbfe428d17cbadbc0ca553a7457795713f` ile birleşti; V24 Real Data CI #62 SUCCESS oldu ve bot `883e680a2564e38f4c08a21bc88aa95b8f164036` commit'iyle kanıtı kalıcılaştırdı. Gerçek cutoff/execution saat politikası evidence içinde açıkça yetkilendirilmemiş (`false`) kalır.

## Açık işler — uygulanacak sıra

1. Gerçek cutoff/execution saat politikasını açıkça kararlaştırıp kayıt altına al. Testteki önceki gün 20:00 / sinyal günü 10:00 değerleri gerçek politika sayılmaz.
2. Yetkilendirilmiş cutoff/execution politikasıyla gerçek 60 tarihsel cutoff için Total Rasyo sonuçlarını üret/doğrula.
3. V24-G readiness raporunu gerçek 60 aylık veriyle çalıştır; sonuç zorunlu olarak `READY` olmalı.
4. Aylık portföyü çalıştırıp holdings, trades, NAV, katkılar ve XU100 karşılaştırmasını yayımla.
5. Tüm kapılar geçince aktif dalı kontrollü biçimde üretime terfi ettir.

Üretim ağırlıkları:

| Modül | Ağırlık |
|---|---:|
| M2 | 0.40 |
| M1 | 0.18 |
| M3 | 0.12 |
| Ek4 | 0.16 |
| Ek1 | 0.08 |
| Ek9 | 0.06 |

`good_count_ge8` ayrıca veto sözleşmesine girer.

## Yayın kuralı

Henüz 5 yıllık getiri veya “başarı” iddiası yayımlanamaz. Birleşik 60-cutoff Total Rasyo skor/sıralama katmanı formal olarak kapalıdır; ancak gerçek cutoff/execution politikası yetkilendirilmeden ve V24-G gerçek veriyle `READY` olmadan üretilen portföy sonucu yayımlanabilir tarihsel performans kanıtı sayılmaz.

Her kapanan aşamada bu belge, ilgili bootstrap belgesi ve CI kanıtı aynı commit zincirinde güncellenmelidir. Yeni bir sohbet açıldığında ilk okunacak dosya budur.
