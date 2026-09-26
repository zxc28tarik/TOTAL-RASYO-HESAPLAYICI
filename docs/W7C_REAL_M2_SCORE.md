# W7-C — W7-B'nin bıraktığı darboğazı kapatıp gerçek, sıfır olmayan bir M2 skoru üretme

Tarih: 2026-09-15. Çalışma dalı: `claude/inspiring-cannon-ecxilb`.
Kanonik pano: [Issue #37](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/37) ·
Sözleşme: [ACTIVE_EXECUTION_LEDGER.md](ACTIVE_EXECUTION_LEDGER.md) §W6/§W7.

W7-B, kanıt-tarihleme kapısını (`PriceLevelActionEvidence.verify`) SPK'nın
kendi arşivlenmiş haftalık bülteniyle gerçekten aştı, ama ikinci, bağımsız bir
kapıda durdu: `NonfinValuationConfig.minimum_peer_count=5`, altı örnek
ticker'ın hepsini `YETERSIZ_MULTIPLE_KAPSAMI` ile reddetti, çünkü o tek
cutoff'ta (2023-08-31) hiçbir NONFIN sektörü CORE'un kendi verisiyle
(kanıt kapısına hiç dokunmadan) beş aynı-çeyrekli ticker'ı bile
`revenue`/`ebit`/`net_income` üçü birden dolu biriktiremiyordu.

**Bu denetim o darboğazı, hiçbir eşiği düşürmeden, gerçek ve doğrulanabilir
kanıtla kapatıyor: yedi ticker'lık kapalı bir örneklemde, üretim ardışık
düzeni değiştirilmeden, gerçek, sıfır olmayan yedi M2 skoru üretiliyor.**

## 1. Sonuç özeti

| Kalem | Değer |
|---|---:|
| Cutoff / sinyal tarihi | 2023-07-31 fiyat / `signal_date=2023-08-01` |
| Sektör / çarpan | XUSIN / PE (`net_income_ttm`, dönem sonu 2023-03-31) |
| Aday ticker | BRSAN, CEMTS, QUAGR, TUKAS, KONYA, VESTL, CCOLA (7) |
| Kanıt kapısını geçen hisse | **7 / 7** |
| Negatif kontrol (erken cutoff, kurcalanmış kaynak) | 7/7 doğru şekilde RET |
| Tam M2 batch replay sonucu | **7 gerçek M2 skoru, 0 ret** |
| PE / PB çarpanı için peer sayısı | 6/6 (her ticker diğer altısını görüyor) |

## 2. Neden bu cutoff/sektör/çarpan

W7-B'nin census'u tek bir cutoff'u (2023-08-31) ölçmüştü. Bu denetim, TTM
(son dört çeyrek, hepsi ayrı ayrı dolu) tamlığını `signal_date=2023-08-01`
için sektör × çarpan bazında tarayarak XUSIN/PE hücresinde **12** ham aday
buldu -- W7-B'nin ölçtüğü herhangi bir hücreden çok daha geniş. Ayıklama:

- **SASA** hariç tutuldu: gerçek bir sermaye artırımı (bülten 28-2023,
  2023-05-17) çapa tarihinden sonra, cutoff'tan önce gerçekleşmiş, cutoff'tan
  önce daha yeni bir KAP sertifikasyonu yok -- kullanılırsa bayat bir çapa
  olurdu.
- **HEKTS, IPEKE, KOZAL**: `kap_share_class_history_v1`'de hiç pay-sınıfı
  gözlemi yok -- bu veri kaynağından çapa kurulamıyor.
- **OYAKC**: tüm gözlemleri `PER_SHARE_UNIT_NOT_TRY` -- nominal pay değeri
  TRY değil, bu yöntemle çözülemiyor.

Geriye kalan yedisi -- BRSAN, CEMTS, QUAGR, TUKAS, KONYA, VESTL, CCOLA --
her biri genuine, `EXPLICIT_CLASS_NOMINALS_RECONCILED`/`usable` bir KAP
pay-sertifikasyonuna sahip ve cutoff'tan önceki en son sertifikasyonları
kullanılabilir durumda.

## 3. Pay sayısı: her zaman ham metinden, önceden hesaplanmış alandan değil

W7-B'nin ZOREN'inde zaten görülmüştü: `share_class_observations.jsonl.gz`'nin
kendi `derived_shares`/`classes` alanları bazen yanlış. Bu denetim artık
**hiçbir zaman** o alanlara güvenmiyor -- `resolve_share_anchor()` her zaman
o gözlemin ham `nominalValueOfShares`/`nominalValuePerShare` KAP API
metnini yeniden ayrıştırıp (virgülü ondalık noktasına çevirip, başka hiçbir
varsayım yapmadan) pay sayısını **kendisi** hesaplıyor, ve bu hesaplanan
değeri `stored_derived_shares` alanıyla karşılaştırıp uyuşmazlığı
`decimal_bug_corrected` bayrağıyla işaretliyor.

Altı ticker için bu iki değer birebir örtüşüyor. **CCOLA'nın 14/05/2019
gözlemi örtüşmüyor:** üç pay-sınıfı satırından ikisi kuruş hassasiyetinde
(3 ondalık basamak) ve artifact'ın önceden hesaplanmış alanı bu iki satırın
ondalık noktasını etkin biçimde düşürüyor (`"51114298.631"` →
`51114298631` gibi), toplamı ~686x şişiriyor. Ham dizgelerin doğru
yeniden hesaplanması **25.437.078.200** pay veriyor -- CCOLA'nın kendi
23/06/2016 gözlemiyle **birebir aynı**, yani iki tarih arasında gerçek bir
sermaye değişikliği yok, tutarlı bir hikaye, bir varsayım değil.

| Ticker | Çapa tarihi | Boşluk (gün) | Pay sayısı | Düzeltildi mi |
|---|---|---:|---:|---|
| QUAGR | 2023-07-24 | 7 | 2.640.000.000 | Hayır |
| TUKAS | 2023-07-24 | 7 | 147.231.000.000 | Hayır |
| BRSAN | 2023-07-27 | 4 | 14.175.000.000 | Hayır |
| CEMTS | 2023-05-31 | 61 | 50.000.000.000 | Hayır |
| CCOLA | 2019-05-14 | 1539 | **25.437.078.200** | **Evet** (ham metinden yeniden türetildi) |
| KONYA | 2019-05-31 | 1522 | 487.344.000 | Hayır |
| VESTL | 2019-04-30 | 1553 | 33.545.627.500 | Hayır |

`derive()`'ın kendi `EXPECTED_DECIMAL_BUG_CORRECTED = {"CCOLA"}` koruması,
bu küme gelecekte değişirse (bir ticker beklenmedik şekilde uyuşmaz hâle
gelirse, ya da CCOLA artık uyuşursa) sessizce geçmek yerine duruyor.

**Önemli not, CORE'un kendi `shares_out` alanı hakkında:** bu yedi ticker
için CORE'un `ISSUED_CAPITAL_OVER_NOMINAL` alanıyla karşılaştırma, ilk bakışta
alarm verici oranlar gösteriyordu (100x'den 68.581x'e). Bu doğrudan bir
red sebebi **değil** -- W7-B'de ZOREN için zaten belgelenen kalıpla aynı:
CORE'un kendi alanı tarihsiz/durağan ve genellikle par-değer varsayımını
(1 TL) doğru uygulamıyor; CEMTS'te doğrudan doğrulandı ki CORE'un rakamı
2018'in **toplam nominal sermaye** tutarına (par-değere bölünmeden) eşit --
hem bayat hem de birim dönüşümü eksik. Bu proje CORE'un bu alanını hiç
kanıt olarak kullanmıyor; yalnızca bu denetimin kendi ham-metin
yeniden-türetmesi güvenilir kaynak.

## 4. Kanıt inşası ve üretim kapısı

Her ticker için `PRICE_LEVEL_ACTION_COVERAGE_V1` manifest'i, çapa
tarihinden 2023-07-31'e kadarki her arşivlenmiş SPK bültenini `sources`
listesine ekliyor. KONYA/VESTL/CCOLA'nın çok yıllı boşlukları, arşivin
tekrar eden yıllık "bağımsız denetime tabi ortaklıklar" numaralı kayıt
listesine her yıl bir kez rastlıyor -- W7-B'de ALFAS/ENKAI/KONTR için zaten
kurulan aynı zararsız yanlış-pozitif sınıfı (bülten 2-2023); 4-2020, 4-2021
ve 2-2022 baskıları bu çalışma paketinde doğrudan incelenip aynı numaralı
liste biçimiyle eşleştiği doğrulandı. CCOLA'nın boşluğu ayrıca ne
sermaye-işlem tablosu satırı ne de bu kayıt listesi olan iki farklı geçiş
buluyor:

- **63-2021** ("D. Diğer Başvuru Sonuçları"): CCOLA'nın %100 sahibi olduğu
  bir yan kuruluşun (Mahmudiye Kaynak Suyu) kolaylaştırılmış birleşme
  onayı -- CCOLA'nın kendi pay sayısına dokunmuyor.
- **56-2022** ("2. Borçlanma Araçları"): CCOLA'nın tahvil/finansman bonosu
  ihracı -- borç, özkaynak değil.

İkisi de bültenin kendi metninden doğrudan okunup kabul edildi.

`PriceLevelActionEvidence.verify()` ve ardından
`materialize_price_level_market_cap()` -- **değiştirilmemiş üretim
fonksiyonları** -- yedi ticker'ın hepsi için gerçek, hash-doğrulanmış piyasa
değeri üretiyor; 7/7 kanıt kapısını geçiyor.

## 5. Negatif kontroller

W7-B'yle birebir aynı iki kontrol, her ticker için ayrı ayrı: erken cutoff
(pencerenin son kaynaklarından önce) ve kurcalanmış gelecek-tarihli kaynak.
**7/7 doğru şekilde `"future source publication"` ile RET** -- geçişin
kapının zayıflatılmasından değil gerçek kanıttan geldiğini gösteriyor.

## 6. İkinci kapı artık gerçekten geçiliyor

Tam `run_historical_pit_nonfin_m2_replay()` çağrısı -- W7-B'nin kullandığı
**aynı**, değiştirilmemiş `nonfin_valuation.kap_bulk_exact_v1.json`
config'iyle -- yedi ticker'ı birbirinin peer'i olarak çalıştırıyor:

- **PE**: 7/7 kullanılabilir, her ticker `peer_count=6`.
- **PB**: 7/7 kullanılabilir, her ticker `peer_count=6`.
- `minimum_coverage_weight=0.5` (PE 0.3 + PB 0.2) her ticker için karşılanıyor.
- **0 ret, 7 gerçek M2 skoru** -- hepsi 0'dan farklı, hepsi kendi ticker'ının
  gerçek fiyat/finansal girdisinden geliyor (aynı sabit değere çökmüyor).

Bu, projenin NONFIN göreli-değerleme yolundan ürettiği **ilk** gerçek M2
skoru kümesi.

## 7. Kapsam, açıkça belirtilmiş

Bu **kapalı, yedi ticker'lık bir örneklem, tam evren koşusu değil.**
Değerleme yolunun uçtan uca, yeterli ve gerçek kanıtla çalıştığını
kanıtlıyor -- 60 tarihsel cutoff'un tamamında her NONFIN hücresinin artık
`minimum_peer_count`'u geçtiğini iddia etmiyor. Bu çalışma paketi aynı
zamanda GENIL (KAP'ta `nominalValuePerShare` alanı hiç yok, bağımsız
kaynaktan doğrulanamıyor), GWIND (2023-07-13'teki gerçek sermaye artışından
sonra, cutoff'tan önce yeni sertifikasyon yok -- bayat çapa) ve TTRAK/KARSN
gibi kendi `anchor_period_end`'i diğerleriyle uyuşmayan adayların **gerçekten
tükenmiş** yollar olduğunu, sadece denenmemiş olmadığını da doğruladı.

## 8. Değişmeyenler

Model matematiği, ağırlıklar, veto, peer/coverage eşikleri hiç değişmedi.
Üretim kodu hiç değişmedi -- yalnızca yeni, hash-pinlenmiş kanıt verisi ve
(bu çalışma paketinin bir parçası olarak) SPK bülten arşivinin 98'den
461 bültene genişletilmesi eklendi (2016-06-24 → 2023-08-31, her yılın
kendi numaralandırması boşluksuz). W7-B'nin kendi altı ticker'ının sonucu
bu genişlemeden **etkilenmedi**: hepsinin çapa tarihi 2022-06-08 veya sonrası,
eklenen bültenlerin hepsi bundan önce.

## 9. Doğrulama

```bash
python scripts/audit_w7c_real_m2_score.py --apply
python scripts/audit_w7c_real_m2_score.py --check
python -m pytest -q tests/test_w7c_real_m2_score.py
python scripts/audit_w7c_mutations.py --output data/audit/w7c_real_m2_score_v1/mutations.json

# W7-B'nin kendi sonucu bu genişlemeden etkilenmediğinin kanıtı
python scripts/audit_w7b_spk_bulletin_evidence.py --apply
python scripts/audit_w7b_spk_bulletin_evidence.py --check
python -m pytest -q tests/test_w7b_spk_bulletin_evidence.py
```

Artifact'lar: `data/audit/w7c_real_m2_score_v1/{archive,anchors,evidence,
gate_results,negative_controls,batch_replay,verdict,mutations,receipt}.json`,
`data/backtest_sources/spk_bulletin_archive_v1/` (461 PDF + manifest, W7-B'yle
paylaşılıyor).

## 10. Sıradaki

Bu yöntem (TTM-tamlık taraması → KAP pay-çapası çözümü ve ham-metin
tutarlılık kontrolü → SPK-bülteni sermaye-işlem-yokluğu taraması) başka
cutoff/sektör/çarpan hücrelerine de uygulanabilir; bu çalışma paketi zaten
XUHIZ/EV_EBIT'te (AKSEN, ENKAI, PGSUS, SMRTG, THYAO -- GWIND'in bayat çapası
yüzünden altıncı ticker eksik kaldı) ve XUHIZ/PE'de (AYDEM, BIMAS, SMRTG,
ZOREN -- GENIL'in çözülemeyen çapası ve GWIND'in bayat çapası yüzünden
5'e ulaşamadı) yakın adayları belgeledi. Gerçekçi sıradaki adım, aynı
sistematik taramayı diğer 59 cutoff'a ve BANK/HOLDING ailelerine genişletmek.
