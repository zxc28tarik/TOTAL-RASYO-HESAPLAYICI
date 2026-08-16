-- v3 geliştirmeleri:
-- 1) M2'ye değerleme desteği kolonu (rsc_val_norm tabanlı ucuzluk bileşeni)
-- 2) Backtest koşularına turnover ve information ratio metrikleri

ALTER TABLE analytics.m2_period_comparison
  ADD COLUMN IF NOT EXISTS m2_valuation_support_score NUMERIC;

ALTER TABLE analytics.backtest_runs
  ADD COLUMN IF NOT EXISTS turnover_avg NUMERIC,
  ADD COLUMN IF NOT EXISTS info_ratio NUMERIC;
