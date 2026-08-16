# KAP BANK toplu uçtan uca fikstürü

Bu dondurulmuş corpus, üç bankayı tek çağrıda ham KAP bildiriminden Total Rasyo
sıralamasına kadar çalıştırır.

Varyantlar:

- `AKBNK`: konsolide kapsam, ortaklık sahiplerine ait tercih edilen kâr etiketi,
  temettü mevcut.
- `GARAN`: solo kapsam, `ifrs-full_ProfitLoss` fallback etiketi, temettü eksik.
- `YKBNK`: konsolide kapsam ve bir tarihsel `RESTATED` dönem.

Komut:

```bash
python -m src.app.cli preview-kap-bank-batch \
  --file test_fixtures/kap_bank_batch_e2e/disclosures.jsonl \
  --contexts-config test_fixtures/kap_bank_batch_e2e/contexts.json \
  --mapping-config config/mkk_kap_financial_facts_mapping.example.json \
  --semantic-config config/kap_bank_semantic_mapping.official_v1.json \
  --derivation-config config/bank_fact_derivation.official_v1.json \
  --analysis-at 2026-05-15T20:00:00+03:00 \
  --anchor 2026-03-31
```

`expected_summary.json`, tam raporun kararlı kabul özetidir. Bu veri gerçek şirket
finansalı iddiası taşımaz; resmî etiket sözleşmelerini kullanan sentetik
entegrasyon corpus'udur.
