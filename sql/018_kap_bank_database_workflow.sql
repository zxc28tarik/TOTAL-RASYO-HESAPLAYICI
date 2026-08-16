-- V8: raw KAP -> all BANKs -> M2/Total Rasyo database workflow support.
-- No new score math is introduced here; indexes preserve point-in-time query plans.

CREATE INDEX IF NOT EXISTS idx_kap_disclosures_source_upper_ticker_published
  ON raw.kap_disclosures (source, upper(ticker), published_at, disclosure_id)
  WHERE ticker IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_kap_disclosures_financial_upper_ticker_published
  ON raw.kap_disclosures (upper(ticker), published_at, disclosure_id)
  WHERE ticker IS NOT NULL
    AND notification_type = 'FINANCIAL_STATEMENT';

CREATE INDEX IF NOT EXISTS idx_kap_facts_profile_upper_ticker_pit_period
  ON raw.kap_financial_facts (
    mapping_profile, mapping_version, upper(ticker), published_at, period_end DESC
  )
  WHERE ticker IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_module_scores_bank_context_pit
  ON analytics.module_scores (
    horizon_days, asof_date, upper(ticker), analysis_at DESC, period_end DESC
  );

COMMENT ON INDEX raw.idx_kap_disclosures_source_upper_ticker_published IS
  'V8 raw KAP BANK batch source/ticker/analysis_at lookup';
COMMENT ON INDEX raw.idx_kap_facts_profile_upper_ticker_pit_period IS
  'V8 point-in-time common BANK anchor lookup by mapping profile/version';
COMMENT ON INDEX analytics.idx_module_scores_bank_context_pit IS
  'V8 non-M2 module context lookup without later intraday leakage';
