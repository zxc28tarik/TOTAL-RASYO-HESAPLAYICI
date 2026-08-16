CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE IF NOT EXISTS core.universe_stocks (
  ticker TEXT PRIMARY KEY,
  company_name TEXT,
  sector_index_code TEXT,
  sector_code TEXT,
  is_active BOOLEAN DEFAULT true,
  updated_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.prices_daily (
  ticker TEXT NOT NULL,
  trade_date DATE NOT NULL,
  open NUMERIC,
  high NUMERIC,
  low NUMERIC,
  close NUMERIC,
  adj_close NUMERIC,
  volume NUMERIC,
  currency TEXT,
  PRIMARY KEY (ticker, trade_date)
);

CREATE TABLE IF NOT EXISTS core.index_prices_daily (
  index_code TEXT NOT NULL,
  trade_date DATE NOT NULL,
  open NUMERIC,
  high NUMERIC,
  low NUMERIC,
  close NUMERIC,
  volume NUMERIC,
  currency TEXT,
  PRIMARY KEY (index_code, trade_date)
);

CREATE TABLE IF NOT EXISTS core.financials_quarterly (
  ticker TEXT NOT NULL,
  period_end DATE NOT NULL,
  version_tag TEXT NOT NULL,
  report_date DATE,
  t0_date DATE,
  t0_source TEXT,
  unit_scale INT DEFAULT 1,

  revenue NUMERIC,
  cogs NUMERIC,
  gross_profit NUMERIC,
  ebit NUMERIC,
  net_income NUMERIC,
  interest_exp NUMERIC,

  total_assets NUMERIC,
  total_equity NUMERIC,
  current_assets NUMERIC,
  current_liabilities NUMERIC,
  cash_and_eq NUMERIC,
  st_investments NUMERIC,
  receivables NUMERIC,
  inventory NUMERIC,
  debt_st NUMERIC,
  debt_lt NUMERIC,

  cfo NUMERIC,
  capex NUMERIC,

  shares_out NUMERIC,
  shares_diluted NUMERIC,

  PRIMARY KEY (ticker, period_end, version_tag)
);

CREATE INDEX IF NOT EXISTS idx_prices_daily_date ON core.prices_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_index_prices_daily_date ON core.index_prices_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_financials_pe ON core.financials_quarterly(period_end);
