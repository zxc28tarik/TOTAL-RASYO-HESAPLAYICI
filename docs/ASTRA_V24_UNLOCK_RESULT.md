# Astra V24 gerçek kilit açma sonucu — 2026-09-06

Bu çalışma, `codex/astra-v24-finalize` dalındaki gerçek 6.000 hücrelik deneysel
replay'i mevcut kaynaklarla ilerletti. Sonuç önemli bir kapsama artışıdır, fakat
geçerli Total Rasyo skoru üretmemiştir. M2 kanıt kapısı korunmuştur.

## Ölçülen fark

| Ölçüm | Korunan v1 | Yeni v2 | Fark |
|---|---:|---:|---:|
| M1 dolu | 0 | 3.267 | +3.267 |
| M2 dolu | 0 | 0 | 0 |
| M3 dolu | 16 | 5.811 | +5.795 |
| Ek1 dolu | 0 | 3.267 | +3.267 |
| Ek4 dolu | 16 | 5.820 | +5.804 |
| Ek9 dolu | 5.598 | 5.598 | 0 |
| En az 1 modül | — | 5.904 | — |
| En az 4 modül | — | 3.174 | — |
| En az 5 modül | — | 3.017 | — |
| Geçerli Total skor | 0 | 0 | 0 |

İki ayrı tam üretim süreci aynı receipt'i ve receipt'te listelenen 64 gzip
artifact'ı bayt düzeyinde aynı üretti. `EXPERIMENTAL_P3_P4_TWO_COMPLETE_REBUILDS_V1`
denetimi PASS'tir.

M3/Ek4'teki eski 16 hücrelik sonuç, üreticinin kapalı M3 paketini tüketmeyip
yalnız tek bir GRTRK/GRTHO olayına bağlı kalmasından kaynaklanıyordu. Yeni akış
paket manifestini önce kapalı olarak doğrular, sonra 210 rotayı tam ticker ve
`valid_from <= gün < valid_to` sözleşmesiyle seçer.

Finansal semantic/CORE akışı 883 kısmi tanı üretmesine rağmen P4'te M1/Ek1'i
daima boş bırakıyordu. Yeni akış yalnız gerçek PIT replay satırlarını taşır;
değer yoksa nötr değer üretmez. Pozitif geniş sektör rotasıyla güvenle NONFIN
denebilen raporlar da CORE replay'e girdiği için kapsam 3.267 hücreye çıktı.
XUMAL; banka, finans, holding ve GYO'yu ayırmadığından aile kanıtı sayılmadı.

## Kalan somut engeller

| Engel | Hücre | Ay | Ticker | Sonuç |
|---|---:|---:|---:|---|
| Ham kapanış baz kanıtı yok | 5.292 | 60 | 193 | M2 yok |
| Tarihli ayarlanmamış nominal pay yok | 5.285 | 60 | 192 | M2 yok |
| BANK tarihli varsayım kanıtı yok | 509 | 60 | 9 | M2 yok |
| Piyasa gözlem penceresi eksik | 402 | 55 | 87 | M3/Ek4/Ek9 kısmi |
| Primary finansal byte yok | 227 | 12 | 100 | M1/Ek1/M2 kısmi |
| Dar ekonomik aile kanıtı yok | 187 | 60 | 5 | XUMAL belirsizliği |
| Semantic mapping çözümsüz | 137 | 60 | 7 | Repo-içi sonraki hedef |
| Action completeness yok | 12 | 12 | 3 | Kanıtlı THB fiyat/payına rağmen M2 yok |

Ham fiyatların ikincil keşif kaynakları ve bazı tarihli pay gözlemleri repo
içinde vardır; bunlar üretim sözleşmesinin istediği baz ve bütünlük kanıtını
tek başına sağlamaz. Yahoo action aday dosyasında split envanteri yoktur ve
önceki resmi taramada bilinen bir KLRHO olayı aylık takvimden kaçmıştır. Bu
nedenle kapıyı gevşetmek look-ahead'i azaltmaz; yalnız kanıtsız M2 üretirdi.
Resmi [Borsa İstanbul DataStore formatı](https://datastore.borsaistanbul.com/assets/files/DataStore_Veri_Bildirim_ve_Kabul_Formatlar%C4%B1.pdf)
yıllık `serart[YYYY].zip` içindeki
`sermaye_[YYYY].xls` raporunu yıl içindeki gerçekleşmiş/planlanmış sermaye
artırım ve azaltımları için tanımlar; [Borsa'nın veri sayfası](https://borsaistanbul.com/veriler/pay-piyasasi-verileri)
geçmiş veriyi satış kanalı olarak sunar. Bu paket repo içinde yoktur. Ücretsiz KAP bildirimleri tekil
olayları kanıtlayabilir, fakat yapılan sorgular 209 ticker × dönem için eksiksiz
negatif-enumeration kanıtı vermedi. Bu nedenle sonraki dürüst M2 işi ya resmi
DataStore paketini edinmek ya da aynı kapsamı hash'li KAP bildirim envanteriyle
yeniden kurup negatif kapsamı ayrıca doğrulamaktır.

Makine-okunur `unlock_matrix.json`, her gerekçenin hücre/ay/ticker kapsamını,
etkilenen modülleri ve tek-engel karşı-olgusal üst sınırını içerir. Üst sınırlar
birbirine eklenemez ve skor iddiası değildir. Örneğin 3.017 hücre beş gerçek
modüle sahiptir; M2'nin tüm bağımsız kanıtları tamamlanırsa puan adayı olabilir.

## Denetim ve sınır

Bağımsız P6-kısmi denetimi 6.000 P3/P4 bağlantısını, 2.115 seçilmiş özgün raporu,
118.434 finansal veri kullanımını, 12 THB kapanışını ve 60 aylık dilimleri
doğruladı. Yeni aile iddiası üreticiden bağımsız olarak paket rotasından yeniden
kuruluyor. Yanlış ticker, hash, sektör kodu, üst üste binen/geçersiz aralık ve
etkinlik öncesi kullanım testleri fail-closed sonuç veriyor.

Geçerli skor ve ay başına en az altı aday olmadığı için P5 strateji motorunu
çalıştırmak dürüst bir performans sonucu üretmezdi. P5 ve tam P6 bu yüzden açık;
P7 authoritative-ready değildir. Profil `EXPERIMENTAL_RISK_ACCEPTED_5Y` olarak
kalır; `main` ve kanonik base değiştirilmez.

Artifact bağlantıları: [v2 receipt](../data/audit/experimental_materialization_v2/receipt.json),
[unlock matrix](../data/audit/experimental_materialization_v2/unlock_matrix.json),
[bağımsız denetim](../data/audit/experimental_materialization_v2/independent_audit.json),
[iki üretim karşılaştırması](../data/audit/experimental_materialization_v2/rebuild_audit.json).
