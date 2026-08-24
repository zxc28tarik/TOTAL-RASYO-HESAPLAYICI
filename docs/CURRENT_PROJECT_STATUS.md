# Total Rasyo Hesaplayıcı — Güncel Proje Durumu

Son doğrulama: **2026-08-23**

Bu belge, sohbet sayfaları değişse bile projenin hedefini ve son doğrulanmış durumunu kaybetmemek için tek başlangıç noktasıdır.

## Tek kanonik GitHub deposu

Depo: [`zxc28tarik/TOTAL-RASYO-HESAPLAYICI`](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI)

| Hat | Dal | Doğrulanmış baş | Anlamı |
|---|---|---|---|
| Üretim | `main` | `84494e29824809b20b5410c8b160ef38f70c27c9` | V24-F üretim fotoğrafı; deneysel tarihsel veri çalışması buraya henüz terfi ettirilmedi |
| Aktif geliştirme | `v24-real-data-work` | `f769ba51db5b3df2ecda9cbcfc4fbb74ee012f52` | En ileri doğrulanmış tarihsel BIST100/PIT/backtest tabanı; birleştirilmiş durum belgeleri, V24-G ve altı sektör ailesinin M2 replay zincirini içerir |

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
| PIT M3 replay motoru | **UYGULANDI — CI ONAYI BEKLİYOR** | DB-free; 63 işlem günü; üretim OLS/shrinkage ve alpha matematiği ortak; tarihsel tarih hizalama canlı yoldan izole; gelecekteki fiyat ve güncel evren reddi |
| Gerçek 60 aylık M3 kaynak paketi | **AÇIK** | tarihsel sektör rotaları ile XU100/sektör endeksi günlük kapanışları henüz hash-kilitli giriş paketi değil |
| V24-G readiness katmanı | **UYGULAMA KAPALI** | report-only, fail-closed; gerçek veriyle `READY` henüz alınmadı |

Aktif dalın son doğrulanmış GitHub kanıt commit'i: [`f769ba5`](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/commit/f769ba51db5b3df2ecda9cbcfc4fbb74ee012f52); test edilen kod `27929b6...`.

- hedefli gerçek-veri sözleşmeleri: **138 geçti**;
- tam regresyon: **1667 geçti**;
- BANK v4.7: **277 geçti, 1 beklenen xfail**;
- iş akışı sonucu: **success**.

M3 özellik dalındaki yerel kabul sonucu: M3 **14/14**, bütün tarihsel PIT replay testleri **75/75**, yerel tam paket **1453 geçti / 224 ortam-bağımlı test atlandı**, BANK v4.7 **277 geçti / 1 beklenen xfail**. Bu sonuç GitHub CI kanıtı oluşana kadar doğrulanmış aktif-dal kanıtının yerine geçmez.

## Açık işler — uygulanacak sıra

1. M3 değişikliğini GitHub CI ile doğrula; 60 ay için tarihsel sektör rotalarını ve XU100/sektör endeksi günlük kapanışlarını kaynak/hash kanıtıyla kilitle.
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
