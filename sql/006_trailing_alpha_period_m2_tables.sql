-- New period-based M1/M2 + trailing alpha tables
-- This migration keeps the old tables for backward compatibility.

CREATE TABLE IF NOT EXISTS analytics.alpha_trailing (
  ticker TEXT NOT NULL,
  asof_date DATE NOT NULL,
  window_days INT NOT NULL,
  start_date DATE,
  end_date DATE,
  stock_ret NUMERIC,
  mkt_ret NUMERIC,
  sec_ret NUMERIC,
  beta_mkt NUMERIC,
  beta_sec NUMERIC,
  alpha_trailing NUMERIC,
  alpha_score NUMERIC,
  alpha_label TEXT,
  PRIMARY KEY (ticker, asof_date, window_days)
);

CREATE TABLE IF NOT EXISTS analytics.period_8q_comparison (
  ticker TEXT NOT NULL,
  asof_date DATE NOT NULL,
  latest_period_end DATE,
  period_count INT,
  score_latest NUMERIC,
  score_prev NUMERIC,
  score_avg_8q NUMERIC,
  score_min_8q NUMERIC,
  score_max_8q NUMERIC,
  score_change_1q NUMERIC,
  score_change_4q NUMERIC,
  score_slope_8q NUMERIC,
  rsc_latest NUMERIC,
  rsc_prev NUMERIC,
  rsc_change_1q NUMERIC,
  good_count_latest INT,
  good_count_prev INT,
  good_count_change_1q INT,
  quality_trend_score NUMERIC,
  quality_trend_label TEXT,
  PRIMARY KEY (ticker, asof_date)
);

CREATE TABLE IF NOT EXISTS analytics.expected_band_periods (
  ticker TEXT NOT NULL,
  period_end DATE NOT NULL,
  version_tag TEXT NOT NULL,
  t0_date DATE NOT NULL,
  horizon_days INT NOT NULL,
  p0 NUMERIC,
  total_ratio_score NUMERIC,
  rsc_core_norm NUMERIC,
  exp_alpha_mid NUMERIC,
  exp_alpha_low NUMERIC,
  exp_alpha_high NUMERIC,
  p_exp_low NUMERIC,
  p_exp_mid NUMERIC,
  p_exp_high NUMERIC,
  bucket_count INT,
  bucket_id INT,
  model_source TEXT,
  asof_date DATE NOT NULL,
  PRIMARY KEY (ticker, period_end, version_tag, horizon_days, asof_date)
);

CREATE INDEX IF NOT EXISTS idx_expected_band_periods_lookup
  ON analytics.expected_band_periods (ticker, asof_date, period_end DESC);

CREATE TABLE IF NOT EXISTS analytics.m2_period_comparison (
  ticker TEXT NOT NULL,
  asof_date DATE NOT NULL,
  latest_period_end DATE,
  prev_period_end DATE,
  current_p_exp_low NUMERIC,
  current_p_exp_mid NUMERIC,
  current_p_exp_high NUMERIC,
  prev_p_exp_low NUMERIC,
  prev_p_exp_mid NUMERIC,
  prev_p_exp_high NUMERIC,
  current_px NUMERIC,
  price_pos_prev_band TEXT,
  price_pos_current_band TEXT,
  band_mid_change_1q NUMERIC,
  price_change_since_prev_period NUMERIC,
  follow_gap_1q NUMERIC,
  band_mid_slope_8q NUMERIC,
  price_to_band_mid_latest NUMERIC,
  price_to_band_mid_avg_8q NUMERIC,
  alpha_trailing_63d NUMERIC,
  m2_band_score NUMERIC,
  m2_follow_score NUMERIC,
  m2_persistence_score NUMERIC,
  m2_alpha_support_score NUMERIC,
  m2_quality_support_score NUMERIC,
  m2_final NUMERIC,
  m2_label TEXT,
  m2_commentary TEXT,
  PRIMARY KEY (ticker, asof_date)
);
