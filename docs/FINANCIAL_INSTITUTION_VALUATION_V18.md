# Banka Dışı Finansal Kuruluş Değerleme Motoru — V18

Faktoring, finansal kiralama (leasing) ve tüketici finansmanı şirketleri için
göreli değerleme motoru.

## Neden ayrı motor

Bu şirketler **BANK veya NONFIN motoruna zorlanamaz**:

- **Bankadan farkı:** mevduat toplamazlar, bilanço yapısı ve sermaye rejimi
  farklıdır; bankanın gerekçelendirilmiş PD/DD modeli (ROE/COE/g) bu şirketlerin
  fonlama yapısına uymaz.
- **NONFIN'den farkı:** bilançoları finansman alacağı ağırlıklıdır; FD/FAVÖK
  mantığı burada anlamsızdır çünkü "faaliyet kârı" finansman geliridir.

## Alt gruplar — ayrı emsal havuzları

```
FACTORING
LEASING
CONSUMER_FINANCE
```

Faktoring şirketi leasing emsaliyle karşılaştırılamaz. Bu **dışlama değil sert
hatadır** (`business_type uyusmuyor`): profil uyumsuzluğu veri kalitesi sorunu,
alt grup karışması çağrı hatasıdır.

## Değerleme yöntemleri

| Yöntem | Rol | Koşul |
|---|---|---|
| PD/DD | **Ana** | her zaman |
| F/K | İkincil | kâr pozitif **ve** `ROE ≥ minimum_pe_roe` |

Ağırlıklı geometrik birleştirme (`pb_weight` 0,70 / `pe_weight` 0,30).
Alt çeyrek – medyan – üst çeyrek bandı, leave-one-out emsal.

**ROE ortalama özkaynak üzerinden** hesaplanır; dönem içi sermaye artışı ROE'yi
şişirmez.

## Aktif kalitesi göstergeleri — bandı DEĞİŞTİRMEZ

| Gösterge | Formül |
|---|---|
| Takip oranı | takipteki alacak / finansman alacağı |
| Karşılık kapsamı | karşılıklar / takipteki alacak |
| Net finansman marjı | net finansman geliri / finansman alacağı |
| Fonlama maliyeti | fonlama gideri / (aktif − özkaynak) |
| Gider/gelir | faaliyet gideri / net finansman geliri |
| Özkaynak tamponu | özkaynak / aktif |
| Sermaye yeterliliği | doğrudan raporlanan |

**Sözleşme:** bu göstergeler fiyat bandını keyfî biçimde şişmez; yalnız `v_conf`
ve tanı alanlarına girer. Bandı aktif kalitesine göre oynatmak, gözlenen emsal
çarpanlarının üzerine ikinci bir özgün görüş eklemek olurdu ve çift sayım
yaratırdı. Sigorta motorundaki teknik gösterge ilkesiyle aynıdır.

Test: `test_aktif_kalitesi_bandi_DEGISTIRMEZ` — iyi ve kötü aktif kalitesinde
`V_mid` birebir aynı, `v_conf` farklı.

**Eksik opsiyonel alan TAHMİN EDİLMEZ**, `None` kalır ve ilgili güven faktörü
hesaba katılmaz.

## Ret nedenleri

| Neden | Anlamı |
|---|---|
| `HEDEF_PARA_BIRIMI_UYUSMUYOR` | config para birimi ile uyuşmuyor |
| `HEDEF_PAY_BAZI_UYUSMUYOR` | fiyat/pay düzeltme bazı farklı |
| `HEDEF_MUHASEBE_PROFILI_UYUSMUYOR` | muhasebe rejimi farklı |
| `HEDEF_FIYAT_BAYAT` | fiyat `max_price_age_days`'i aştı |
| `HEDEF_FINANSAL_BILGI_BAYAT` | finansal tablo `max_statement_age_days`'i aştı |
| `HEDEF_KAYNAK_GUVENI_DUSUK` | kaynak güveni eşiğin altında |
| `HEDEF_PD_DD_MODEL_ARALIGI_DISINDA` | PD/DD `[minimum_pb, maximum_pb]` dışında |
| `HEDEF_OZKAYNAK_TAMPONU_YETERSIZ` | aşırı kaldıraç |
| `YETERSIZ_FINANSAL_KURULUS_EMSALI` | geçerli emsal `minimum_peer_count` altında |
| `YETERSIZ_DEGERLEME_YONTEMI` | yöntem sayısı `minimum_method_count` altında |

Emsal dışlama nedenleri (`excluded_peers`): `PERIOD_MISMATCH`,
`METRICS_PROFILE_MISMATCH`, `ACCOUNTING_PROFILE_MISMATCH`, `CURRENCY_MISMATCH`,
`SHARE_BASIS_MISMATCH`, `FIYAT_BAYAT`, `FINANSAL_BILGI_BAYAT`,
`KAYNAK_GUVENI_DUSUK`, `PD_DD_MODEL_ARALIGI_DISINDA`,
`OZKAYNAK_TAMPONU_YETERSIZ`.

## İki eksenli M2

```
M2 = valuation_axis_weight × s_val_effective + follow_axis_weight × follow_score
s_val_effective = 0,50 + (valuation_score − 0,50) × v_conf
```

Değerleme skoru güvenle **nötre daraltılır**, çarpılmaz: bir skorun nötrü 0,5'tir,
0 değil. Kaynak: `FINANCIAL_INSTITUTION_PB_PE_TWO_AXIS_V1`.

## Şema

Migration: `sql/026_financial_institution_valuation.sql`

```
core.financial_institution_metrics_snapshots        (immutable trigger)
analytics.financial_institution_valuation_periods
analytics.financial_institution_valuation_rejections (ret defteri)
analytics.financial_institution_m2_scores
analytics.latest_financial_institution_m2_scores     (view)
```

`CHECK` kısıtları: çeyrek sonu, `period_end ≤ published_at`, özkaynak ≤ aktif,
alacak ≤ aktif, takip ≤ alacak, **karşılık varsa takip zorunlu** (aksi halde
kapsam oranı türetilemez), `OK` ise `0 < V_low ≤ V_mid ≤ V_high`.

## Sektör yönlendirmesi

`sector_code` alanı **alt tür** taşıyabilir; alt tür aile adı değildir:

```
FACTORING | LEASING | CONSUMER_FINANCE  ->  FINANCIAL
NON_LIFE  | LIFE_PENSION                ->  INSURANCE
```

Eşleme olmadan bu şirketler geniş `XUMAL` endeksine veya `NONFIN`'e düşerdi.

## CLI ve Make

```bash
make ingest-fi-metrics          # örnek JSONL, --no-persist
make run-fi-batch               # config doğrulama, --no-persist
make self-audit-fi-valuation    # 15.000 senaryo

python -m src.app.cli ingest-fi-metrics --file <jsonl>
python -m src.app.cli run-fi-batch --analysis-at <ts> \
    --valuation-config config/financial_institution_valuation.pb_pe_v1.json \
    --routing-config config/sector_routing.v1.json
```

## Öz denetim

`scripts/self_audit_financial_institution_valuation.py` — 15.000 senaryo.
Sigorta denetiminden farkı: **orkestratör ve kalıcılık** katmanlarını da tarar.

| Senaryo grubu | Adet |
|---|---|
| Geçerli değerleme | 4.000 |
| Kontrollü yetersizlik | 2.500 |
| Bayat / profil reddi | 2.500 |
| Emsal sırası değişmezliği | 2.000 |
| **Orkestratör izolasyonu** | 2.000 |
| **Kalıcılık sözleşmesi** | 1.500 |
| Doğrulama bypass reddi | 500 |

Sonuç: `PASS`, 0 kontrolsüz exception, 0 sessiz kabul.
Kanıt: `docs/SELF_AUDIT_FINANCIAL_INSTITUTION_V18.json`
