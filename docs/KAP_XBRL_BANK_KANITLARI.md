# KAP XBRL BANK Kalem Kanıtları — V1

Bu belge `config/kap_bank_semantic_mapping.official_v1.json` içindeki ilk üretim eşlemesinin resmî KAP finansal rapor sayfasında görülen etiketlere dayandığını kaydeder.

## Doğrulanan XBRL etiketleri

Örnek resmî KAP banka finansal raporu: `https://kap.org.tr/en/Bildirim/1601304`

| Canonical alan | KAP/XBRL etiketi | Resmî sayfadaki açıklama |
|---|---|---|
| `TOTAL_EQUITY` | `ifrs-full_Equity` | ÖZKAYNAKLAR / EQUITY |
| `ISSUED_CAPITAL` | `ifrs-full_IssuedCapital` | Ödenmiş Sermaye / Issued capital |
| `NET_INCOME` | `ifrs-full_ProfitLossAttributableToOwnersOfParent` | Grubun Kârı (Zararı) |
| `NET_INCOME` fallback | `ifrs-full_ProfitLoss` | Dönem Net Kârı veya Zararı |
| `DIVIDENDS_PAID` | `ifrs-full_DividendsPaid` | Dağıtılan Temettü / Dividends Paid |

Rapor ayrıca hisse başına kâr açıklamasında **1 TL nominal değerli beher pay** ifadesini kullanır. Bu nedenle `ISSUED_CAPITAL → pay sayısı` dönüşümü kod içine gömülü değildir; `share_nominal_value = 1` olarak sürümlü türetim config’inde açıkça tutulur.

## Ölçek sözleşmesi

Örnek sayfa sunum para birimini `1.000 TL` olarak gösterir. Ham fact normalizer bu ölçeği `unit_scale=1000` ile TRY değerine çevirdikten sonra semantic eşleme çalışmalıdır. BANK türeticisi ölçekleme yapmaz; yalnız ölçeklenmiş semantic değerleri kullanır.

## Point-in-time ve kapsam

- `ProfitLossAttributableToOwnersOfParent` mevcutsa `ProfitLoss` etiketinden önce gelir.
- `CONSOLIDATED`, `SOLO` kapsamından önce gelir.
- Aynı dönem ve öncelikte çelişkili değerler sessizce seçilmez; mapping reddedilir.
- `ifrs-full_Equity` toplam özkaynağı temsil eder. Azınlık payı bulunan konsolide bankalarda özkaynak sahiplerine ait ayrı bir bilanço etiketi resmî ürün payload’ında sunuluyorsa mapping’in sonraki sürümünde birinci önceliğe alınmalıdır.

## Sınır

Bu config, resmî KAP HTML sayfasında görünür etiketleri kanıtlar. Gerçek MKK API ürününün JSON yolları ve boyut adları ürün dokümanı/API anahtarı olmadan tahmin edilmemiştir.
