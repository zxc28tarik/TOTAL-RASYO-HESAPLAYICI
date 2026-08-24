# Total Rasyo Hesaplayıcı — Güncel Proje Durumu

Son doğrulama: **2026-08-24**

Bu belge, sohbet sayfaları değişse bile projenin hedefini ve son doğrulanmış durumunu kaybetmemek için tek başlangıç noktasıdır.

## Tek kanonik GitHub deposu

Depo: [`zxc28tarik/TOTAL-RASYO-HESAPLAYICI`](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI)

| Hat | Dal | Doğrulanmış baş | Anlamı |
|---|---|---|---|
| Üretim | `main` | `84494e29824809b20b5410c8b160ef38f70c27c9` | V24-F üretim fotoğrafı; deneysel tarihsel veri çalışması buraya henüz terfi ettirilmedi |
| Aktif geliştirme | `v24-real-data-work` | `20b2c1f9afb5aa7c04a8a42fdf91384484d9a14d` | PR #17 merge başı; M3, PIT Ek4 ve PIT Ek1/good-count CLOSED |

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
| V24-G readiness katmanı | **UYGULAMA KAPALI** | report-only, fail-closed; gerçek veriyle `READY` henüz alınmadı |

Son otomatik kanıt commit'i [`fa1fbbc`](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/commit/fa1fbbc25e24f4a4edd187c6c485143d3a6cfc47)'dir. Ek1 için denetlenen head [`d4a227d7`](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/commit/d4a227d7c7956f0dfcf5f6506bc65b99d56a9f79), bit-bit aynı ağacı taşıyan merge commit'i [`20b2c1f9`](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/commit/20b2c1f9afb5aa7c04a8a42fdf91384484d9a14d) ve başarılı PR iş akışı [CI #47](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/actions/runs/32774085210)'dir.

- pandas 2.2.3 / numpy 1.26.4 uyumluluk kapısı: **91 geçti**;
- hedefli gerçek-veri sözleşmeleri: **229 geçti**;
- tam regresyon: **1758 geçti**;
- BANK v4.7: **277 geçti, 1 beklenen xfail**;
- iş akışı sonucu: **success**.

PR #13'ün canlı beta uyumluluğu, tarihsel `pct_change(fill_method=None)` davranışı ve pandas sürüm kapısı Claude tarafından gerçek diff ve mutasyon testleriyle bağımsız olarak denetlendi; blocker/major kalmadan birleştirildi.

PR #15'in gerçek M3 veri paketi iki bağımsız denetimden geçti. İkinci turda `GRTRK -> GRTHO` kimlik zinciri yol+SHA256 ve mutasyon testleriyle sertleştirildi; GitHub CI #41 ve merge-sonrası CI kanıtı yeşil tamamlandı.

PR #16'nın PIT Ek4 replay motoru bağımsız diff ve mutasyon denetiminden blocker/major olmadan geçti. GitHub PR CI #44; pinned pandas 2.2.3, şema, hedefli gerçek-veri sözleşmeleri, tam regresyon ve BANK v4.7 kapılarının tamamında yeşildir. Merge commit'i `b24208141cce48e6009943d514d04e9ef5e18693` olup PR head içeriğiyle bit-bit eşleşir.

PR #17'nin PIT Ek1/good-count replay motoru bağımsız diff ve sekiz mutasyon denetiminden blocker/major olmadan geçti. GitHub PR CI #47; pinned pandas 2.2.3'te 91, hedefli pakette 229, tam regresyonda 1.758 test ve BANK v4.7 kapılarının tamamında yeşildir. Merge commit'i `20b2c1f9afb5aa7c04a8a42fdf91384484d9a14d` olup denetlenen PR head ağacıyla bit-bit eşleşir.

## Açık işler — uygulanacak sıra

1. PIT-safe **Ek9** replay: 63 günlük getiri oynaklığını tarihsel olarak kur.
2. Altı modülü üretim ağırlıklarıyla birleştirip 60 cutoff için Total Rasyo sonucu ve sıralaması üret.
3. Gerçek cutoff/execution saat politikasını açıkça kararlaştırıp kayıt altına al. Testteki önceki gün 20:00 / sinyal günü 10:00 değerleri gerçek politika sayılmaz.
4. V24-G readiness raporunu gerçek 60 aylık veriyle çalıştır; sonuç zorunlu olarak `READY` olmalı.
5. Aylık portföyü çalıştırıp holdings, trades, NAV, katkılar ve XU100 karşılaştırmasını yayımla.
6. Tüm kapılar geçince aktif dalı kontrollü biçimde üretime terfi ettir.

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

Henüz 5 yıllık getiri veya “başarı” iddiası yayımlanamaz. PIT Ek9, tam tarihsel Total Rasyo otoritesi, gerçek cutoff politikası ve V24-G `READY` kapanmadan üretilen sonuç deneme/fixture sayılır.

Her kapanan aşamada bu belge, ilgili bootstrap belgesi ve CI kanıtı aynı commit zincirinde güncellenmelidir. Yeni bir sohbet açıldığında ilk okunacak dosya budur.
