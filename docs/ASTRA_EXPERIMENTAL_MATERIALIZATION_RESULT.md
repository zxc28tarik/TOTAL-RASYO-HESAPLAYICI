# Gerçek deneysel materialization sonucu — 2026-09-06

**6.000 hücrelik P3 ve 60 aylık P4 artifact zinciri iki kez üretildi ve bağımsız
olarak doğrulandı. Geçerli Total Rasyo skoru sıfırdır; yatırım stratejisinin
performansı tamamlanmış değildir.** Profil `EXPERIMENTAL_RISK_ACCEPTED_5Y`;
production/authoritative kaynak sınırları ve üretim matematiği korunmuştur.

## Gerçekten üretilenler

| Katman | Sonuç |
|---|---|
| Yeniden oluşturulmuş katalog | 26 özgün hash eşleşmeli arşiv, 15.109 rapor; hash'i değişmiş 2 arşiv kabul edilmedi |
| Finansal semantic veri | 5.052 rapor, 199.969 veri; ana/önceki ticker/birleşik kaynak kodu paketlerinin tümü iki bağımsız ham okumada aynı |
| P2 yeni aday kümesi | 833 HOLDING + 280 GYO = 1.113 hücre; kullanılabilir tam M2 sonucu 0; eski 993/981 sayıları yeniden üretildi denmiyor |
| P3 | 6.000/6.000 tarihsel üye; 5.633 hücrede görünür kendi dönemine ait finansal veri; 0 hazır + 6.000 açık ret |
| CORE tanısı | 4.798 hücrede gerçek çeyrek/CORE oranları; 883 hücrede kısmi RSC/M1/Ek1 kayıtları; tam CORE+VAL yerine kullanılmadı |
| P4 | 60 dosya × 100 üye; her satır P3 hash'ine bağlı; geçerli skor/sıra/alış kararı yok |
| Piyasa modülleri | M3: 16, Ek4: 16, Ek9: 5.598 gerçek dolu sonuç |
| Tekrarlanabilirlik | 64 P3/P4 gzip dosyası ve receipt iki ayrı hesaplama sürecinde birebir aynı |
| Bağımsız kaynak denetimi | 22 özgün arşivdeki 2.115 seçilmiş rapor, 118.434 finansal veri kullanımı, 12 THB fiyatı, 6.000 bağlantı ve 60 aylık dilim doğrulandı |

P3'ün ready-or-explicit-rejection ve P4'ün score-or-explicit-rejection
**artifact kapsamı geçmiştir**. Bu kabul, boş sayısal sıralamaları başarılı
bir stratejiye dönüştürmez. P3 kabul etiketi yalnız bu kapsam için
`PASS_WITH_EXPLICIT_VERSION_ENUMERATION_RISK` olarak kaydedilmiştir.

## 6.000 ret neden eski global katalog engeli değildir

Eksik özgün türetilmiş katalog, hiçbir hücrede global finansal ret gerekçesi
olmadı. Yeni katalog özgün/recovered diye adlandırılmadı; immutable manifest
değiştirilmedi. M2 önkoşullarındaki ayrık dağılım şöyledir:

| Gerçek önkoşul sınırı | Hücre |
|---|---:|
| Tarihsel ekonomik aile kanıtı yok | 4.241 |
| BANK ailesi belli; tarihli model varsayımı kanıtı yok | 509 |
| Diğer ailelerde fiyat/pay normalizasyonu kanıtı eksik | 1.238 |
| THB ham kapanışı ve tarihli pay kanıtı var; action completeness yok | 12 |
| **Toplam** | **6.000** |

Finansal veri içermeyen 367 hücrenin dağılımı ayrıca 227 eksik primary bytes,
137 kullanılabilir kendi-dönem semantic veri reddi ve 3 cutoff-sonrası yayın
reddidir. Piyasa sektör endeksi kanıtı 5.984 hücrede eksiktir. Genel finansal
şema, otomatik NONFIN sınıflandırmasına çevrilmedi; güncel sektör/ticker veya
finansal snapshot ile doldurma yapılmadı.

`dependency_details/dependency_matrix.csv.gz` her hücrenin somut önkoşullarını;
`market_observation_gaps.jsonl.gz` Ek9'un 402 eksik gözlem penceresini ve tarihlerini
kaydeder. Örneğin 9 Nisan 2024 fiyat boşlukları sonraki aylardaki Ek9 kapsamını
etkiler. Farklı kaynak/bazdaki fiyatlar sessizce birleştirilmedi.

Birleşik KAP kodları (`GARAN-TGB`, İş Bankası/Kardemir sınıf listeleri gibi)
468 ek raporu finansal kapsama getirdi. Özgün kaynak kimliği korundu;
finansal şirket bağı, sınıflar arasında fiyat veya nominal pay aktarım kanıtı
sayılmadı. Yetkili yarım günlerde 12:40 cutoff'u ayrıca düzeltildi ve sınandı.

## P5 nakit tanısı ve V24-G

Gerçek P4 retleriyle mevcut portföy motoru çalıştırıldı. 60 gerçek asgari ücret
dönemine göre aylık 2× net ücret katkısı, boş işlem listesi ve nakit korunumu
iki kez üretildi; çıktı bağımsız Decimal hesabıyla uzlaştırıldı.

| Tanısal değer | TL |
|---|---:|
| Toplam katkı / nakit NAV | 1.715.833,40 |
| Aynı katkılarla XU100 OPEN-DCA değeri | 3.259.122,08 |
| Nakit eksi XU100 | −1.543.288,68 |

İşlem sayısı 0, nakit getirisi %0'dır. Benchmark ikincil Yahoo OPEN/CLOSE
gözlemlerine, kesirli endeks birimine, sıfır ücret/faize dayanır. Son değerleme
**1 Temmuz 2026 kapanışıdır; Temmuz ay sonu değildir**. Bu sayılar Total Rasyo
strateji performansı olarak sunulamaz; `p5_strategy_performance_completed=false`.

Mevcut V24-G frame auditor'ü 60 ayın tamamını inceledi ve **BLOCKED** verdi.
52 ayda 174 gerçek execution hücresi eksik; ayrıca authoritative FULL_UNIVERSE
registry bulunmuyor. Benchmark, ücret, cutoff ve üye setinde bulgu yok.
Başarılı registry kaydı uydurulmadı, veritabanına yazılmadı.

## P2 ve P7 araştırması

INVES/ASGYO sabit sermaye gözlemleri ve KLRHO 650 milyon → 1,625 milyar pay
geçişi ek gerçek rapor/bildirimlerle uzlaştırıldı. Eşit iki bilanço değeri veya
bilinen bir bedelsiz olay, tüm olası pay değişikliklerinin eksiksiz tarandığını
kanıtlamaz. 12 THB hücresindeki action-completeness reddi bu nedenle korunur.

KORTS 2022 raporunun ilk bildirimi **1122417** ile düzeltmesi **1126845** gerçek
KAP kaynaklarından alındı. 437 sayısal hücrenin dördündeki ana ortaklık/azınlık
payı düzeltmesi doğrulandı. Özgün 2022 yıllık toplu arşiv yalnız sonraki sürümü
içeriyor. Bu pozitif kurtarma, bütün tarihsel düzeltilmiş/silinmiş sürümlerin
enumeration'ını kanıtlamaz; P7 authoritative kapısı açıktır.

## Doğrulama ve kabul sınırı

Hesaplama kodu `62a3152` üzerinde altı GitHub CI çalışması başarılıdır:
Linux/PostgreSQL **2.170 passed / 7 skipped**, Windows **1.940 passed / 233 skipped**.
Production mutation denetimi **26/26 killed**; BANK v4.7 **277 passed / 1 xfailed**.
Son ek bildirim-kimliği denetimi **43 test**, portföy/readiness kontrolleri **12 test**
ile doğrulandı. Artifact commit'inin kendi CI sonucu GitHub PR #40'ta ayrıca izlenir.

Bağımsız P6 sonucu `P6_PARTIAL_SOURCE_TO_REJECTION_AUDIT_V1` için PASS'tir.
Her finansal sayı ayrı bir ikinci mapper ile tekrar hesaplanmadı ve eksik
M2/CORE+VAL girdileriyle çalışmayan sektör motorları tamamlandı sayılmadı.
Tam P5/P6 ve authoritative P7 kapanışı yoktur. PR #40 taslak kalır; `main`
değiştirilmedi ve merge/force-push yapılmadı.

Kaynaklar ve çalışma komutları:
[runbook](EXPERIMENTAL_MATERIALIZATION_RUNBOOK.md),
[bağımsız inceleme](../data/audit/experimental_materialization_v1/independent_review.md),
[P3/P4 receipt](../data/audit/experimental_materialization_v1/receipt.json),
[iki üretim karşılaştırması](../data/audit/experimental_materialization_v1/rebuild_audit.json),
[P6 kaynak denetimi](../data/audit/experimental_materialization_v1/independent_audit.json),
[nakit tanısı](../data/audit/experimental_portfolio_v1/portfolio_diagnostic.json),
[readiness](../data/audit/experimental_readiness_v1/readiness_report.json).
