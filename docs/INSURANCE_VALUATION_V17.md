# V17 — Sigorta PD/DD + F/K Değerleme Motoru

## Amaç

V17, sigorta şirketlerini sanayi şirketlerinin `EV/EBIT` veya satış çarpanlarıyla
değerlendirmek yerine sigorta faaliyetinin ekonomik yapısına uygun ayrı bir
point-in-time değerleme hattına taşır.

İlk üretim sürümü iki alt grubu birbirinden kesin olarak ayırır:

- `NON_LIFE`: elementer / hayat dışı sigorta,
- `LIFE_PENSION`: hayat ve emeklilik.

Bu ayrım emsal seçiminde zorunludur. İki grup birbirinin emsal dağılımına
katılmaz.

## Resmî veri çerçevesi ve proje tercihi

KAP sigorta finansallarında yazılan prim, teknik bölüm dengesi ve yatırım
gelirleri gibi alanlar ayrı biçimde raporlanır. Hayat/hayat dışı/emeklilik
faaliyetleri de aynı ekonomik yapı gibi kabul edilmez. TFRS 17 geçişi nedeniyle
muhasebe profili ve sürümü V17 veri sözleşmesinde açık alanlardır.

Bunlardan türetilen proje tercihi şöyledir:

- temel göreli değerleme `PD/DD` ile yapılır,
- pozitif ve karşılaştırılabilir kâr varsa `F/K` ikinci yöntem olur,
- teknik marj, birleşik oran ve yatırım geliri bağımlılığı değerleme bandını
  keyfî biçimde yükseltmez; güven katsayısını ve tanıları etkiler,
- farklı muhasebe profilleri aynı emsal grubunda karıştırılmaz.

## Veri sözleşmesi

Kaynak kayıtları `core.insurance_metrics_snapshots` tablosuna değişmez kayıtlar
olarak alınır. Her kayıt en az şu alanları taşır:

- ticker, finansal dönem ve yayın zamanı,
- `NON_LIFE` veya `LIFE_PENSION`,
- muhasebe profili ve sürümü,
- para birimi ve fiyat/pay bazı,
- toplam özkaynak ve pay sayısı,
- TTM net kâr,
- TTM yazılan prim,
- TTM teknik sonuç,
- TTM yatırım geliri,
- kaynak belge kimliği ve SHA256,
- metrik profili/sürümü ve kaynak güveni.

`NON_LIFE` için kazanılmış prim, net hasar ve faaliyet giderleri birlikte
verilirse birleşik oran hesaplanır. Üçlü kısmi verilemez. İlk sürümde
`LIFE_PENSION` kayıtlarında birleşik oran alanları kabul edilmez.

Muhasebe profili teslim edilen config'te:

```text
TFRS17_LOCAL_STATUTORY / version 1
```

olarak sabitlenmiştir. Bu bir resmî sınıflandırma adı iddiası değil, proje içi
karşılaştırılabilirlik sözleşmesidir.

## Point-in-time seçim

Analiz anında yalnız:

- `published_at <= analysis_at`,
- doğru metrik/muhasebe profili ve sürümü,
- doğru para birimi ve `share_basis`,
- geçerli takvim çeyrek sonu,
- tazelik sınırları içindeki finansal veri ve fiyat

kullanılır.

Aynı dönem birden fazla kaynak belgesiyle geldiyse kayıt kimliği kaynak belge
kimliğini de içerir. Aynı kimliğin farklı kanonik içerikle yeniden gelmesi
sessizce kabul edilmez.

## Hesaplanan göstergeler

```text
Piyasa değeri          = fiyat × pay sayısı
Defter değeri / pay    = özkaynak / pay sayısı
PD/DD                  = piyasa değeri / özkaynak
F/K                    = piyasa değeri / TTM net kâr
ROE                    = TTM net kâr / özkaynak
Teknik marj            = TTM teknik sonuç / TTM yazılan prim
Yatırım bağımlılığı    = |yatırım geliri| / max(|net kâr|, küçük taban)
Birleşik oran          = (net hasar + faaliyet gideri) / kazanılmış prim
```

Negatif veya sıfır kârda F/K yöntemi kullanılmaz. Config minimum yöntem sayısı
bir olduğundan yeterli PD/DD emsali varsa yalnız PD/DD ile kontrollü değerleme
üretilebilir.

## Emsal ve değerleme bandı

Emsaller yalnız aynı:

- alt sigorta grubu,
- finansal dönem,
- muhasebe profili/sürümü,
- metrik profili/sürümü,
- para birimi ve pay bazı

şartlarını sağlar.

Hedef şirket kendi emsal dağılımına katılmaz. PD/DD ve uygunsa F/K için emsal
alt çeyrek, medyan ve üst çeyrekleri hesaplanır. Her yöntemin şirket özkaynak veya
kâr bazından ürettiği hisse başı değer bantları ağırlıklı geometrik olarak
birleştirilir.

Teslim edilen başlangıç ağırlıkları:

```text
PD/DD : 0,65
F/K   : 0,35
```

Bunlar gerçek BIST dağılımıyla nihai kalibrasyon değildir.

## Güven katsayısı

`v_conf` şu bileşenlerden etkilenir:

- emsal sayısı,
- finansal tablo ve fiyat tazeliği,
- kaynak güveni,
- yöntem kapsamı,
- değerleme bandı genişliği,
- teknik faaliyet kalitesi.

Teknik kalite değerlendirmesi teknik marj, birleşik oran ve yatırım geliri
bağımlılığını kullanır. Teknik kalite değer bandını değiştirmez; yalnız bandın
ne kadar güvenle M2'ye taşınacağını etkiler.

## İki eksenli M2

```text
Sigorta M2 = güvenle küçültülmüş değerleme ekseni
           + dönemsel fiyat/band takip ekseni
```

Kaynak etiketi:

```text
INSURANCE_PB_PE_TWO_AXIS_V1
```

Günlük M2 override sırası:

```text
HOLDING → GYO → INSURANCE → NONFIN → BANK
```

Bu sıra uygulama döngüsüdür; yalnız ilgili sektörde sonuç bulunduğu için sektörler
birbirinin sonucunu pratikte ezmez. Sigorta override'ı yalnız tam `analysis_at`
kesiminde okunur; tarih-only çalışmada gelecekte hesaplanmış sonuç sızmaz.

## PostgreSQL tabloları

Migration:

```text
sql/025_insurance_valuation.sql
```

Tablolar:

- `core.insurance_metrics_snapshots`
- `analytics.insurance_valuation_periods`
- `analytics.insurance_valuation_rejections`
- `analytics.insurance_m2_scores`
- `analytics.latest_insurance_m2_scores`

Kaynak metrik tablosu immutable'dır. Yalnız idempotent tekrar alımında
`inserted_at` tazelenebilir; başka alan değişikliği ve silme reddedilir.

Aynı analiz yeniden çalıştırıldığında güncel rapor otoritatiftir: yeni ret alan
şirketin eski başarılı değerleme/M2 satırı transaction içinde temizlenir; yeni
başarı alan şirketin eski ret kaydı silinir.

## Komutlar

Kuru örnek veri doğrulaması:

```bash
python -m src.app.cli ingest-insurance-metrics \
  --file data/insurance_metrics.example.jsonl \
  --no-persist
```

Batch prova:

```bash
python -m src.app.cli run-insurance-batch \
  --analysis-at 2026-08-05T20:00:00+03:00 \
  --valuation-config config/insurance_valuation.pb_pe_v1.json \
  --routing-config config/sector_routing.v1.json \
  --no-persist
```

Öz denetim:

```bash
make self-audit-insurance-valuation
```

## Doğrulama

```text
Entegrasyon testleri       : 689 passed
Saf BANK motoru            : 277 passed, 1 xfailed
Sigorta öz denetimi        : 15.000 / 15.000 PASS
Kontrolsüz exception       : 0
Sessiz bozuk kabulü        : 0
```

## Dış sınırlar

Henüz yapılmamış dış doğrulamalar:

- çalışan PostgreSQL 16 üzerinde `025` migration ve transaction provası,
- gerçek KAP sigorta finansallarını bu açık TTM sözleşmesine dönüştüren adaptör,
- BIST sigorta şirketlerinin güncel alt grup evreni,
- gerçek dağılımla çarpan sınırı/ağırlık/emsal sayısı kalibrasyonu,
- hayat/emeklilik için daha ayrıntılı ürün ve yükümlülük ölçüleri,
- sermaye yeterlilik verisinin güvenilir ve dönemsel canlı kaynağı.

Bu sınırlar tamamlanmadan V17 matematiği üretim garantisi veya yatırım tavsiyesi
olarak yorumlanmamalıdır.

## Commit haritası

```text
36792ac  Saf sigorta PD/DD + F/K motoru ve metrik sözleşmesi
1886a5c  Batch, CLI, PostgreSQL ve günlük M2 bağlantısı
f723feb  15.000 senaryolu öz denetim ve V17 devir belgeleri
```
