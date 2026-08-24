# Total Rasyo Hesaplayıcı — Güncel Proje Durumu

Son doğrulama: **2026-08-24**

Bu belge, sohbet sayfaları değişse bile projenin hedefini ve son doğrulanmış durumunu kaybetmemek için tek başlangıç noktasıdır.

## Tek kanonik GitHub deposu

Depo: [`zxc28tarik/TOTAL-RASYO-HESAPLAYICI`](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI)

| Hat | Dal | Doğrulanmış baş | Anlamı |
|---|---|---|---|
| Üretim | `main` | `84494e29824809b20b5410c8b160ef38f70c27c9` | V24-F üretim fotoğrafı; deneysel tarihsel veri çalışması buraya henüz terfi ettirilmedi |
| Aktif geliştirme | `v24-real-data-work` | `4fce01482b7bae9574f055c2382a8f43ea86f3f3` | PR #13 sonrası doğrulanmış tarihsel taban; M3 replay, canlı beta uyumluluk koruması ve kalıcı pandas 2.2.3 CI kapısını içerir |

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
| Gerçek 60 aylık M3 kaynak paketi | **AÇIK — SÖZLEŞME UYGULANDI** | fail-closed hash/lineage/6000 rota/252 günlük kapanış kapıları kuruldu; gerçek ham ve kanonik kaynaklar henüz eklenmedi |
| V24-G readiness katmanı | **UYGULAMA KAPALI** | report-only, fail-closed; gerçek veriyle `READY` henüz alınmadı |

Aktif dalın son doğrulanmış GitHub kanıt commit'i: [`4fce014`](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/commit/4fce01482b7bae9574f055c2382a8f43ea86f3f3); test edilen kod `533aaaf7b9ef7f3050d63d3b80e7376bb4ae59ef`.

- pandas 2.2.3 / numpy 1.26.4 M3 uyumluluk kapısı: **17 geçti**;
- hedefli gerçek-veri sözleşmeleri: **155 geçti**;
- tam regresyon: **1684 geçti**;
- BANK v4.7: **277 geçti, 1 beklenen xfail**;
- iş akışı sonucu: **success**.

PR #13'ün canlı beta uyumluluğu, tarihsel `pct_change(fill_method=None)` davranışı ve pandas sürüm kapısı Claude tarafından gerçek diff ve mutasyon testleriyle bağımsız olarak denetlendi; blocker/major kalmadan birleştirildi.

## Açık işler — uygulanacak sıra

1. Uygulanan M3 kaynak sözleşmesini bağımsız denetimden geçir; ardından 60 ay için gerçek tarihsel sektör rotalarını ve XU100/sektör endeksi günlük kapanışlarını ham kaynak + kanonik dosya + hash + deterministik dönüşüm kanıtıyla kilitle.
2. PIT-safe **Ek4** replay: 20 günlük hisse getirisi eksi sektör endeksi getirisi üretimini kur.
3. PIT-safe **Ek1** ve `good_count_ge8` replay: RSC özetinden üretim semantiğini tarihsel olarak kur.
4. PIT-safe **Ek9** replay: 63 günlük getiri oynaklığını tarihsel olarak kur.
5. Altı modülü üretim ağırlıklarıyla birleştirip 60 cutoff için Total Rasyo sonucu ve sıralaması üret.
6. Gerçek cutoff/execution saat politikasını açıkça kararlaştırıp kayıt altına al. Testteki önceki gün 20:00 / sinyal günü 10:00 değerleri gerçek politika sayılmaz.
7. V24-G readiness raporunu gerçek 60 aylık veriyle çalıştır; sonuç zorunlu olarak `READY` olmalı.
8. Aylık portföyü çalıştırıp holdings, trades, NAV, katkılar ve XU100 karşılaştırmasını yayımla.
9. Tüm kapılar geçince aktif dalı kontrollü biçimde üretime terfi ettir.

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

Henüz 5 yıllık getiri veya “başarı” iddiası yayımlanamaz. Gerçek 60 aylık M3 kaynak kapsamı, PIT Ek4/Ek1/Ek9, tam tarihsel Total Rasyo otoritesi, gerçek cutoff politikası ve V24-G `READY` kapanmadan üretilen sonuç deneme/fixture sayılır.

Her kapanan aşamada bu belge, ilgili bootstrap belgesi ve CI kanıtı aynı commit zincirinde güncellenmelidir. Yeni bir sohbet açıldığında ilk okunacak dosya budur.
