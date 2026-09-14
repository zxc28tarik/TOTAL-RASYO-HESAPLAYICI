# W7-A — üretim kodunun kendi kanıt-tarihi kapısı: gerçek M2 hâlâ BLOCKED

Tarih: 2026-09-14. Çalışma dalı: `claude/inspiring-cannon-ecxilb`.
Kanonik pano: [Issue #37](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/37) ·
Sözleşme: [ACTIVE_EXECUTION_LEDGER.md](ACTIVE_EXECUTION_LEDGER.md) §W6/§W7.

**Bu belge W6'nın "peer kapısı açıldı" bulgusunu düzeltmiyor — o bulgu doğru
kalıyor — ama onun ardından gerçek M2 üretmeye çalışırken üretim kodunun
kendi içinde, W6'nın ölçmediği, daha derin bir kapı bulundu. O kapı kapalı
kalıyor ve bugünkü kanıtla açılamıyor.**

## 1. Ne oldu

W6'dan sonra SMRTG 2023-07-31 hücresi için gerçek M2 üretmeye çalışıldı.
Üretim replay fonksiyonu (`run_historical_pit_nonfin_m2_replay`) gerçekten
çağrıldı — aynı sektör kodunu (`XUHIZ`) taşıyan 21 NONFIN adaydan 9'u W6'nın
envanteriyle sertifikalanabilir bulundu; bunlardan 8'i artifact'ın kendi
güvensiz pay sayısıyla çapraz doğrulandı (oranlar 1,00×–4,00× arası, hepsi
gerçek bonus/sermaye artışlarıyla açıklanabilir; TTKOM 3070× oranla açık bir
veri anomalisi olarak elendi).

Ama üretim kodu bu 8 peer'i **kabul etmedi**. Sebep W6'nın ölçmediği bir şey:
`src/analytics/price_level_action_evidence.py`'deki `PriceLevelActionEvidence.verify()`
fonksiyonu, kullanılan her kanıt kaynağının **kendi yayın zaman damgasının**
analiz cutoff'undan önce veya ona eşit olmasını (`published_at <= cutoff`)
koşulsuz olarak istiyor.

Bugün (2026) KAP'tan çekilen bir kayıt — içeriği ne kadar doğru ve kapsamlı
olursa olsun — **kendi yayın zaman damgasını bugünün tarihini taşıyarak**
getirir. 2023-07-31 cutoff'u için bu, her zaman "gelecekten kaynak" (`future
source publication`) hatasıyla reddedilir.

## 2. Gerçek koda karşı kanıtlandı, varsayılmadı

`scripts/audit_w7a_evidence_dating_gate.py` bunu **gerçek üretim fonksiyonunu
çağırarak** kanıtlıyor, okumaya dayanarak değil:

- **Vaka A (dürüst zaman damgası):** AKSEN için W6'nın gerçek, hash'e bağlı
  kapsama penceresi kullanılarak bir `PriceLevelActionEvidence` inşa edildi;
  kaynağın yayın zamanı **gerçek yakalama anı** (`2026-09-14T09:23:04Z`)
  olarak dürüstçe işaretlendi. Sonuç: **reddedildi** —
  `"future source publication"`.
- **Vaka B (kontrol, geri tarihlenmiş):** Aynı manifest, aynı kaynak
  baytları, tek fark: yayın zamanı cutoff'un kendisi (`2023-07-31T18:10:00+03:00`).
  Sonuç: **kabul edildi**.

İki vaka arasındaki tek fark tarih olduğu için, ret sebebinin manifest
şekli/hash zinciri değil, **tam olarak bu tarih kontrolü** olduğu kanıtlanmış
oldu.

## 3. Neden bu, SMRTG'nin kendi sertifikasyonunda sorun çıkarmadı

SMRTG'nin kendi `zero_interval_share_basis_v1/action_coverage_manifest.json`'ı
incelendiğinde görülüyor ki onun aralığı **sıfır uzunluklu**
(`shares_basis_date == complete_through == 2023-07-31`). Bu durumda
KAP'ın **kendi tarihli** pay sınıfı bildirimi (`published_at:
2023-07-31T17:49:42+03:00` — cutoff'tan önce, gerçek bir tarihsel belge) hem
pay kaynağı hem tamlık kaynağı olarak tek başına yeter, çünkü sıfır uzunluklu
bir aralıkta enumere edilecek hiçbir zaman aralığı yoktur.

**Sıfır olmayan hiçbir aralık için bu numara işlemez** — enumerasyonun
"tamam, hiçbir şey olmadı" diyebilmesi için pencerenin **kapanmış olması**
gerekir, ve bunu tasdik eden kaynağın kendisi pencerenin kapandığı anda ya da
sonrasında üretilir — yani cutoff'tan **sonra**. Modern bir sorgu bunu asla
cutoff'tan önce üretemez.

## 4. Sonuç: W5'in sistemik sınırı hâlâ geçerli

W5, sıfır-aralıklı sertifikasyon rotasının cutoff başına **en fazla 1 ticker**
verdiğini ölçmüştü (56 cutoff'ta 0, 4 cutoff'ta 1). W6 gerçek kanıt üretti ve
"aksiyon yok" sorusuna cevap verdi, ama üretim kodunun kendi tarih kapısı
nedeniyle **bu kanıt üretim replay'ine hiç giremiyor**. Sonuç:

**Üretim-kabul edilebilir peer sayısı, önceki gibi, her cutoff'ta en fazla 1
kalıyor — 5 asgari şartın altında. Gerçek tarihsel NONFIN M2, peer'e dayalı
göreli değerleme yoluyla, 60 cutoff'un hepsinde hâlâ BLOCKED.**

## 5. Bu neden "kod değiştir" ile çözülmez

Bu bir hata değil — kasıtlı bir ileri-bilgi (look-ahead) koruması. Kontrolü
gevşetmek ("aslında içerik doğruysa tarih önemli değil" demek) tam olarak
defterin en temel kuralının ihlali olurdu: *mevcut/bugünkü bilgi, tarihsel PIT
kanıtının yerine geçemez.* Üretim kodu — haklı olarak — "bugün sorgulanan bir
indeksin *tamlığı*, sorgulandığı andan önce bilinemez" ilkesini uyguluyor.

## 6. Yeniden açma koşulu

Gerekli olan, **dönemin kendisinde yayımlanmış**, cutoff'tan önce veya o anda
tarihli, arşivlenmiş resmî bir tamlık kaynağı — geriye dönük bir sorgu değil.
P2 araştırması bunun tek adayını (KAP kurumsal işlem takvimi) zaten
tüketmişti: takvim yalnız ileriye dönük çalışıyor, geçmiş aylar için `[]`
dönüyor — arşiv değil.

## 7. Değişmeyenler

Hiçbir M2 üretilmedi, hiçbir hücre onarılmadı, hiçbir eşik gevşetilmedi,
üretim kodu değişmedi. Canlı 131 CORE / 48 M2 / 11 Ek9 / 2 Total / 805 ret
aynen korunuyor.

## 8. Doğrulama

10 hedef test PASS (`tests/test_w7a_evidence_dating_gate.py`), `--check` bayt
düzeyinde yeniden üretiyor. Yerel tam regresyon (ilgili alt küme) 100 passed.

```bash
python scripts/audit_w7a_evidence_dating_gate.py --apply
python scripts/audit_w7a_evidence_dating_gate.py --check
python -m pytest -q tests/test_w7a_evidence_dating_gate.py
```

Artifact'lar: `data/audit/w7a_evidence_dating_gate_v1/{attempt,verdict,receipt}.json`.
