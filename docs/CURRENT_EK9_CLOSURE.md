# Current Ek9 kapsamı — 12 Eylül 2026

Başlangıç: `fad20cccc88f230628999c1a06770c0f7329a12c`, PR #40,
`codex/astra-v24-finalize`. Yalnız current/live hattı işlendi. Analiz fiyat
kesimi 8 Eylül 2026; 12 Eylül kaynak yakalamaları tarihsel PIT kanıtı sayılmadı.

| Çıktı | Başlangıç | Sonuç |
|---|---:|---:|
| Kullanılabilir valuation | 49 | 49 |
| FOLLOW | 48 | 48 |
| M2 | 48 | 48 |
| Ek9 | 11 | 55 |
| Total / ranking | 2 | 46 |
| Açık Total reddi | 805 | 761 |

44 öncelikli şirketin her birinde 10 Haziran–8 Eylül penceresindeki tek eksik
gözlem 7 Eylül'dü. XU100 yalnız işlem takvimi otoritesidir; Ek9'da benchmark
getirisi veya benchmark fiyatıyla ikame yoktur. Üretim gereği 64 gerçek fiyat,
63 günlük getiri, `std(ddof=1)`, 0,06 volatilite tavanı aynen korundu.

İlk ücretsiz Yahoo yakalaması 44/44 şirkette aynı günü döndürmedi.
Kaydedilmiş Mynet/Forinvest tarihsel tabloları gerçek eksik kapanışı sağladı.
Mynet tarihleri Türkçe ve İngilizce ay adlarıyla aynı takvim gününe ayrıştırıldı.
Bütün ortak pencere ya Yahoo ham kapanışlarıyla ya Yahoo düzeltilmiş
kapanışlarıyla, tablonun iki ondalık fiyat gösterimi üzerinden eşleşmek zorundadır.
Satır bazında farklı fiyat temelleri seçilmez. DOAS ve ENERY'nin geçmiş temettü
düzeltmeleri nedeniyle tabloları düzeltilmiş pencereyle eşleşir.

Eksik güne gerçek kaynak kapanışı `close` olarak yazıldı; `adj_close` boş
bırakıldı. Yeni düzeltilmiş fiyat türetilmedi. Mevcut üretim
`COALESCE(adj_close, close)` kuralı ancak eksik günün iki komşu Yahoo gözleminde
ham ve düzeltilmiş fiyatlar aynıysa kullanıldı. Eski Yahoo gözlemleriyle bütün
ortak pencerenin tutarlılığı da ayrıca doğrulandı.

İlk Mynet geçişi 40 Ek9 ve 42 Total verdi. Kaydedilmiş dört ret, ay dili
(KCAER/KONYA) ve geçmiş temettü fiyat temeli (DOAS/ENERY) düzeltilerek, yeni ağ
çağrısı olmadan yeniden işlendi. Sonuç 44/44 yeni Ek9 ve 46 gerçek Total'dır.
Kaynak/pencere uyuşmazlığı, eksik gün, hatalı ticker, yinelenen tarih, NaN,
sonsuz veya pozitif olmayan fiyat açık ret üretir.

M1/M3/Ek1/Ek4 ve mevcut 11 Ek9 korundu. 72.949 eski fiyat gözlemi korundu;
yalnız 44 yeni tarihli fiyat eklendi. CSV yeniden yazımının en büyük sayısal
yuvarlama farkı 2,85e-14'tür. Mevcut 11 Ek9 artifact alanı birebir korundu;
önceki iki Total sonucu yalnız sıralamadaki sıra numarası açısından değişti.
Üretim `src/` dosyaları, ağırlıklar, veto, karar ve ranking kuralları değişmedi;
nötr doldurma, eşik gevşetme ve ağırlık yeniden dağıtımı yapılmadı.

Üst run/readiness receipt'lerindeki eski M2=0 / Total=0 özeti gerçek current
artifact'lardan yenilendi. Önceki Windows CRLF hash'leri yalnız özgün CRLF
baytları yeniden kurulup kayıtlı SHA256 birebir eşleşiyorsa kabul edildi;
fiyat içeriği mutasyonları reddedilir. Evren meta hash'i bu doğrulamadan sonra
Git'teki LF baytlarına bağlandı. Finansal arşiv veya pay yakalaması tekrarlanmadı;
modül dışındaki cached readiness kanıtları korundu. Readiness'in veritabanı
ve aile bağlama retleri, Total skor üretiminin ret defterinden ayrıdır.

## Kanıtlar

- `data/live/current_ek9_gap_repair_v1/`: 44 Yahoo pencere kaydı, 44 ham Mynet
  HTML gzip'i, ticker dizini, ticker denetimi, 55 Ek9 sonucu ve hash receipt'i.
- `data/audit/current_ek9_closure_v1/`: başlangıç artifact'ları, özgün yakalama
  receipt'i ve bağımsız yeniden hesaplama doğrulaması.
- Kaynak toplama kod head'i: `c75d471afcaea501001d0c7cf0891249d6e6ea4b`.
- Actions run: 34701961016; artifact: 10300189340;
  ZIP SHA256: `43163326eab66765205a0fc817919e3f7af415a8ee13aec6ccade812980fd266`.
- Yerel doğrulama: 34 passed, 1 deselected. Deselected test yalnız bu kısıtlı
  yerel checkout'ta bulunmayan resmi quote PDF'ini gerektirir; GitHub tam
  checkout CI'ında çalışır. Kaynak toplama head'inde 34 Ek9 testi geçti;
  final head'in CI sonucu PR #40'ta ayrıca kayıtlıdır.

## Yeniden üretim

Başlangıç stock/modules/receipt ve Total artifact'larını aynı current yollarına
kopyalayın. Son kaynak paketi `data/live/current_ek9_gap_repair_v1/` içindeki
ham yakalamaları, audit dizinindeki özgün `capture_receipt.json` ve yakalama
aşaması `capture_ek9_scores.csv`, `capture_ticker_audit.jsonl`,
`capture_captures.json` ile ayrı bir replay dizinine koyun; dosyaları özgün
receipt isimlerine döndürün. Özgün SHA256'lar doğrulanır.

```bash
python scripts/repair_current_ek9_gaps.py --replay-dir /path/to/capture --combine-current
```

Final current artifact'larıyla tekrar çalıştırma yeni fiyat yakalamadan mevcut
55 Ek9 / 46 Total'ı korur. Yeni tarihli canlı çalışma için bütün current
kaynakların aynı kesimle yenilenmesi gerekir.

## Kalan somut sınır

48 M2 şirketinden Total üretemeyen yalnız DEVA ve JANTS kaldı. İkisinde de
M1, Ek1, Ek9 ve good_count eksik. Ek9 tek başına bu iki şirketi Total'a geçirmez.
Diğer 759 evren şirketinde M2 eksikliği sürüyor. Bütün BIST kapsamı ve tarihsel
M2/P5/P6 kapanışı ilan edilmedi.
