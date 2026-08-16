CREATE TABLE IF NOT EXISTS analytics.ratios_quarterly (
  ticker TEXT NOT NULL,
  period_end DATE NOT NULL,
  version_tag TEXT NOT NULL,
  ratio_name TEXT NOT NULL,
  ratio_value NUMERIC,
  is_na BOOLEAN NOT NULL DEFAULT false,
  PRIMARY KEY (ticker, period_end, version_tag, ratio_name)
);

CREATE TABLE IF NOT EXISTS analytics.rsc_scores_quarterly (
  ticker TEXT NOT NULL,
  period_end DATE NOT NULL,
  version_tag TEXT NOT NULL,
  ratio_name TEXT NOT NULL,
  pillar TEXT,
  score_1_10 NUMERIC,
  level_percentile NUMERIC,
  trend_bonus NUMERIC,
  is_na BOOLEAN NOT NULL DEFAULT false,
  PRIMARY KEY (ticker, period_end, version_tag, ratio_name)
);

CREATE TABLE IF NOT EXISTS analytics.rsc_summary_quarterly (
  ticker TEXT NOT NULL,
  period_end DATE NOT NULL,
  version_tag TEXT NOT NULL,
  rsc_core_norm NUMERIC,
  rsc_val_norm NUMERIC,
  good_count_ge8 INT,
  score_mean NUMERIC,
  score_std NUMERIC,
  PRIMARY KEY (ticker, period_end, version_tag)
);

CREATE TABLE IF NOT EXISTS analytics.beta_estimates (
  ticker TEXT NOT NULL,
  t0_date DATE NOT NULL,
  beta_mkt NUMERIC,
  beta_sec NUMERIC,
  r2 NUMERIC,
  n_obs INT,
  PRIMARY KEY (ticker, t0_date)
);

CREATE TABLE IF NOT EXISTS analytics.alpha_realized (
  ticker TEXT NOT NULL,
  t0_date DATE NOT NULL,
  horizon_days INT NOT NULL,
  stock_ret NUMERIC,
  mkt_ret NUMERIC,
  sec_ret NUMERIC,
  alpha_real NUMERIC,
  PRIMARY KEY (ticker, t0_date, horizon_days)
);

CREATE TABLE IF NOT EXISTS analytics.decile_map (
  sector_code TEXT NOT NULL,
  horizon_days INT NOT NULL,
  bucket_count INT NOT NULL,
  bucket_id INT NOT NULL,
  mu_alpha NUMERIC,
  sigma_alpha NUMERIC,
  n_obs INT,
  window_end DATE NOT NULL,
  PRIMARY KEY (sector_code, horizon_days, bucket_count, bucket_id, window_end)
);

CREATE TABLE IF NOT EXISTS analytics.expected_price_band (
  ticker TEXT NOT NULL,
  t0_date DATE NOT NULL,
  horizon_days INT NOT NULL,
  p0 NUMERIC,
  exp_alpha_mid NUMERIC,
  exp_alpha_low NUMERIC,
  exp_alpha_high NUMERIC,
  p_exp_mid NUMERIC,
  p_exp_low NUMERIC,
  p_exp_high NUMERIC,
  bucket_count INT,
  bucket_id INT,
  PRIMARY KEY (ticker, t0_date, horizon_days)
);

CREATE TABLE IF NOT EXISTS analytics.module_scores (
  ticker TEXT NOT NULL,
  asof_date DATE NOT NULL,
  period_end DATE,
  horizon_days INT NOT NULL,
  m1 NUMERIC,
  m2 NUMERIC,
  m3 NUMERIC,
  ek1 NUMERIC,
  ek3 NUMERIC,
  ek4 NUMERIC,
  ek5_dilution NUMERIC,
  ek9 NUMERIC,
  base_score NUMERIC,
  final_score NUMERIC,
  good_count_ge8 INT,
  decision TEXT,
  veto_flag BOOLEAN,
  PRIMARY KEY (ticker, asof_date, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_ratios_pe ON analytics.ratios_quarterly (period_end);
CREATE INDEX IF NOT EXISTS idx_rsc_summary_pe ON analytics.rsc_summary_quarterly (period_end);
CREATE INDEX IF NOT EXISTS idx_alpha_t0 ON analytics.alpha_realized (t0_date);
