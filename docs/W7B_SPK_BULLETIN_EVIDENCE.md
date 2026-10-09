# W7-B — SPK haftalık bülteniyle kanıt-tarihleme kapısını gerçek veriyle aşma

Tarih: 2026-09-14. Çalışma dalı: `claude/inspiring-cannon-ecxilb`.
Kanonik pano: [Issue #37](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/37) ·
Sözleşme: [ACTIVE_EXECUTION_LEDGER.md](ACTIVE_EXECUTION_LEDGER.md) §W6/§W7.

W7-A, üretim kapısının (`PriceLevelActionEvidence.verify`) **bugün sorgulanan**
her tamlık kaynağını -- içeriği ne kadar doğru olursa olsun -- kayıtsız şartsız
reddettiğini kanıtlamıştı: kaynağın kendi `published_at` damgası her zaman
bugünün tarihi olur, ve bu her geçmiş cutoff'tan sonradır. Bu denetim, W7-A'nın
bu bulgusunu **değiştirmeden**, kalitatif olarak **farklı türde bir kaynak**
dener: SPK'nın (Sermaye Piyasası Kurulu) 2005'ten beri arşivlenen haftalık
bülteni. Her sayı **kendi yayım anında** basılmış, ayrı, tarihli bir belge --
bugün yapılan bir sorgu değil.

**Sonuç dürüstçe kaydedilmeli: kapı gerçekten aşıldı, ama tam M2 skoru henüz
üretilmedi -- ayrı ve çok daha sıradan bir sebeple.**

## 1. Sonuç özeti

| Kalem | Değer |
|---|---:|
| Arşivlenen SPK bülteni | 98 (2022-06-09 → 2023-08-31, boşluksuz numaralama) |
| Kanıt kapısını geçen hisse (gerçek `materialize_price_level_market_cap`) | **6 / 6** |
| Negatif kontrol (erken cutoff, kurcalanmış kaynak) | 6/6 doğru şekilde RET |
| Tam M2 batch replay sonucu | 0 skor, 6/6 `YETERSIZ_MULTIPLE_KAPSAMI` |
| PB çarpanı için peer sayısı | 5/5 (eşik karşılandı) |
| PE / EV_EBIT / PS çarpanı için peer sayısı | 5'in altında |

## 2. Kaynak: SPK haftalık bülteni

`https://www.spk.gov.tr/spk-bultenleri/...` -- her yılın kendi arşiv
sayfasında, sayfalama (`?s=N`) ile gezilerek eksiksiz indirildi. Her bülten
sayfası kendi "Bülten No" ve "Yayımlanma" tarihini taşıyor (bazı yıllarda
`YYYY/NN`, bazılarında `YYYY-NN` ayracıyla -- ikisi de destekleniyor). 2021 ve
öncesi farklı bir tablo şablonu kullanıyor (`Tarih` / `Sayı` / `Bülten`
sütunları); o şablon da ayrı ayrıştırıcıyla okunuyor.

Her bültenin **"A. İzahname / İhraç Belgesi Onaylanan Sermaye Piyasası
Araçları" → "2. Halka Açık Ortaklıkların Pay İhraçları"** bölümü, o hafta SPK
tarafından onaylanan her bedelli/bedelsiz sermaye artırımını satır satır
listeliyor -- bu proje için ihtiyaç duyulan tam olay türü. Bir şirketin bu
bölümde **hiç** geçmemesi, o hafta için sermaye artırımı olmadığının resmi,
dönemsel, tarihli kanıtı.

**Çapraz doğrulama:** SMRTG'nin kendi 605.880.000 pay sermaye artırımı,
bülten 41-2023'te (20/07/2023) bu tam bölümde bulunuyor -- W5'in bağımsız
KAP kanıtıyla (basis date 2023-07-31) birebir tutarlı.

**Bilinen özellik, düzeltilmiş:** SPK en az bir kez (52-2022 ve 53-2022,
ikisi de 02/10/2022) aynı takvim gününde iki ayrı bülten yayımlamış --
arşiv bunu `by_date` altında liste olarak tutuyor, tekilleştirmiyor.

## 3. Altı örnek hisse ve neden bunlar

163 NONFIN tickerdan, XUHIZ sektör-eş grubunda (SMRTG'nin kendi grubu),
KAP'ın kendi sertifikalı pay-sınıfı gözleminden cutoff'a (2023-08-31) en
kısa boşluğu olan altısı seçildi -- bülten sayısını (ve dolayısıyla PDF
metin taraması maliyetini) sınırlı tutmak için:

| Ticker | Çapa tarihi | Pay sayısı | Boşluk (gün) | Taranan bülten |
|---|---|---:|---:|---:|
| SMRTG | 2023-07-31 | 605.880.000 | 31 | 22 |
| ZOREN | 2023-06-15 | 500.000.000.000 | 77 | ~55 |
| GESAN | 2023-04-12 | 460.000.000 | 141 | ~35 |
| ALFAS | 2022-11-21 | 46.000.000 | 283 | 80 |
| KONTR | 2022-09-07 | 200.000.000 | 358 | 80 |
| ENKAI | 2022-06-08 | 6.000.000.000 | 449 | 98 |

**Önemli düzeltme:** Pay sayısı `share_class_observations.jsonl.gz`'deki
zaten-doğru-hesaplanmış `derived_shares` alanından alınıyor, KAP'ın ham
`nominalValueOfShares` alanının doğrudan toplanmasından **değil** -- ZOREN'in
nominal pay değeri 1 TL değil 0,01 TL, bu yüzden ham değerlerin toplamı
gerçek pay sayısının 1/100'ü çıkar (5 milyar vs gerçek 500 milyar). Bu hata
geliştirme sırasında yakalandı ve düzeltildi; script artık ham değeri
kendisi yeniden türetmiyor, doğrudan zaten-doğrulanmış alanı kullanıyor.

## 4. Kanıt inşası ve üretim kapısı

Her ticker için `PRICE_LEVEL_ACTION_COVERAGE_V1` manifest'i, çapa tarihinden
cutoff'a kadarki **her** arşivlenmiş bülteni `sources` listesine ekliyor
(`enumeration_complete: true` -- bu, script'in gerçekten taradığı kapsamı
temsil ediyor, üretim kapısının minimumundan fazlasını). `events: []`
(sermaye artırımı bulunmadı). `completeness_source_ref`, pencerenin son
bülteni.

`PriceLevelActionEvidence.verify()` ve ardından
`materialize_price_level_market_cap()` -- **değiştirilmemiş üretim
fonksiyonları** -- altı ticker'ın hepsi için gerçek, hash-doğrulanmış
piyasa değeri üretiyor:

| Ticker | Piyasa değeri (TL) |
|---|---:|
| KONTR | 15.932.720.947,27 |
| SMRTG | 15.308.567.845,92 |
| ALFAS | 6.647.000.000,00 |
| GESAN | 42.688.001.403,81 |
| ZOREN | 2.424.999.952.316,28 |
| ENKAI | 196.799.995.422,36 |

Bu, projenin sıfır olmayan bir aralık için üretim yolundan geçen **ilk**
gerçek piyasa değeri üretimi.

## 5. Negatif kontroller -- kapı gerçekten mi geçti

İki kontrol, her ticker için ayrı ayrı çalıştırıldı:

- **Erken cutoff**: aynı kanıt paketi, pencerenin son birkaç kaynağından
  önceki bir cutoff'a karşı doğrulanmaya çalışıldı → 6/6 `"future source
  publication"` ile doğru şekilde RET.
- **Kurcalanmış kaynak**: manifest'teki son kaynağın `published_at`'i
  2026'ya taşındı → 6/6 doğru şekilde RET.

Bu, geçişin kapının zayıflatılmasından değil gerçek, dönemsel-çağdaş
kanıttan geldiğini gösteriyor.

## 6. İkinci, bağımsız kapı: yeterli çarpan kapsamı

Tam `run_historical_pit_nonfin_m2_replay()` çağrısı, altı ticker'ı birbirinin
peer'i olarak, gerçek PIT finansallarla (mevcut `core_diagnostics.jsonl.gz`
artifact'ından) çalıştırıyor. Sonuç: **0 M2 skoru**, hepsi
`YETERSIZ_MULTIPLE_KAPSAMI` ile reddediliyor.

Sebep basit ve W7-A'nınkinden **tamamen farklı**: `NonfinValuationConfig`
her çarpan için `minimum_peer_count=5` istiyor. Altı ticker'la, her hedef
için en fazla 5 peer var -- ve PB çarpanı gerçekten 5/5'e ulaşıyor
(kullanılabilir), ama PE, EV_EBIT ve PS çarpanları için yeterli sayıda
**geçerli** (sadece mevcut değil) peer değeri yok.

**Kök sebep, kanıt kapısıyla ilgisiz, daha derin bir katmanda:** altı
ticker'ın CORE'daki (2023-06-30 dönem sonu) en son çeyreğinde `revenue`
5/6'sında, `net_income` 3/6'sında, `ebit` 1/6'sında **`None`**. Bu sessiz
bir veri kaybı değil -- her `None` alanın kendi gerekçe kodu var, örn.
KONTR için `"net_income": "YTD_PERIOD_START_MISMATCH"`: CORE'un YTD-fark
türetmesi, karşılaştırdığı iki dönemin başlangıç tarihleri uyuşmadığında
**tahmin üretmek yerine reddediyor** -- projenin fail-closed felsefesiyle
tam tutarlı, düzeltilecek bir hata değil.

Bu alanın gerçek boyutu bu tek cutoff'ta ölçüldü: 24 XUHIZ adayının
**yalnız 1'i** (ALFAS), 34 XUSIN adayının **yalnız 3'ü** üçünü birden
(`revenue`+`ebit`+`net_income`) dolu taşıyor -- `minimum_peer_count=5`'in
**hiçbir NONFIN sektöründe, salt CORE'un kendi verisiyle, kanıt kapısına
hiç dokunmadan bile** karşılanamadığı anlamına geliyor.

**Bu bir ölçek sorunu, W7-A'nın tarihleme sorununun bir tekrarı değil --
ama saf "birkaç ticker daha çöz" kadar basit de değil.** §9'a bakın: aynı
cutoff'ta (2023-08-31) tüm 24 XUHIZ adayının **yalnız 1'i** (ALFAS),
34 XUSIN adayının **yalnız 3'ü**, revenue+EBIT+net_income üçünü birden
dolu taşıyor -- kanıt kapısına hiç dokunmadan, salt CORE'un kendi
finansal türetme katmanında. SPK-bülteni yöntemi bu ayrı, daha derin
sınırı ortadan kaldırmıyor; yalnız onu görünür kılıyor.

## 7. Değişmeyenler

Canlı 131 CORE / 48 M2 / 11 Ek9 / 2 Total / 805 ret aynen korunuyor. Üretim
kodu hiç değişmedi -- yalnız yeni, hash-pinlenmiş kanıt verisi eklendi.
W7-A'nın kendi bulgusu (bugün sorgulanan kaynak kayıtsız şartsız reddedilir)
da değişmedi; bu denetim o koda dokunmadı, sadece niteliksel olarak farklı
bir kaynak sağladı.

## 8. Doğrulama

21 hedef test PASS, 9/9 mutasyon KILLED, `--check` bayt düzeyinde aynı
receipt.

```bash
python scripts/audit_w7b_spk_bulletin_evidence.py --apply
python scripts/audit_w7b_spk_bulletin_evidence.py --check
python -m pytest -q tests/test_w7b_spk_bulletin_evidence.py
python scripts/audit_w7b_mutations.py --output data/audit/w7b_spk_bulletin_evidence_v1/mutations.json
```

Artifact'lar: `data/audit/w7b_spk_bulletin_evidence_v1/{archive,evidence,
gate_results,negative_controls,batch_replay,field_completeness_census,verdict,
mutations,receipt}.json`,
`data/backtest_sources/spk_bulletin_archive_v1/` (98 PDF + manifest),
`data/backtest_sources/spk_bulletin_resolved_shares_v1/` (6 ticker × pay
sertifikası kaynağı + çapa).

## 9. Sıradaki

Naif okuma ("birkaç ticker daha çöz") §6'nın sayılarıyla düzeltilmeli: bu
cutoff'ta hiçbir NONFIN sektörü, kanıt kapısına hiç dokunmadan, salt
CORE'un kendi türetmesiyle bile 5 tam-finansallı ticker biriktiremiyor
(XUSIN 34'te 3, XUHIZ 24'te 1). SPK-bülteni yöntemi kaç ticker'ın
*kanıtlanabilir* olduğunu artık pratikte sınırsız genişletebiliyor (arşiv
30 yıla, tüm BIST'e ölçekleniyor) -- ama darboğaz artık kanıt değil, CORE'un
YTD-türetmesinin **hangi ticker/çeyrek kombinasyonlarında** dönem
başlangıçları uyuştuğu. Gerçekçi sıradaki adım iki parçalı:

1. **Ölçüm, tüm 60 cutoff'a genişletilsin:** bu doküman tek bir cutoff'u
   (2023-08-31) ölçtü. `revenue`/`ebit`/`net_income` üçünün birden dolu
   olduğu ticker sayısı ay ay değişebilir -- bazı aylarda bazı sektörlerin
   5 eşiğini doğal olarak geçtiği bir cutoff bulunabilir; bulunamazsa bu da
   kendi başına kaydedilmesi gereken bir sonuçtur.
2. **Böyle bir cutoff/sektör bulunursa**, o hücrelerin SPK-bülteni
   boşluğu (kısa olan öncelikli) bu denetimin aynı, artık kanıtlanmış
   yöntemiyle kapatılır.

Bu denetimin kendi `derive()`'ı, sıfır olmayan bir `m2_score_count`
çıkarsa sessizce geçmek yerine `UNEXPECTED_M2_SCORE_PRODUCED` ile duruyor --
yani bu eşik aşıldığında bu denetimin kendisinin de anlatısı güncellenmeli,
sonucu sessizce kabul etmemeli.
