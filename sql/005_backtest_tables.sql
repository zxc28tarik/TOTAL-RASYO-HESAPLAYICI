CREATE TABLE IF NOT EXISTS analytics.backtest_runs (
  run_id TEXT PRIMARY KEY,
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  rebalance TEXT NOT NULL,
  hold_days INT NOT NULL,
  n_rebalances INT,
  avg_n_al INT,
  cum_return NUMERIC,
  ann_return NUMERIC,
  ann_vol NUMERIC,
  sharpe NUMERIC,
  max_drawdown NUMERIC,
  bench_cum_return NUMERIC
);

CREATE TABLE IF NOT EXISTS analytics.backtest_timeseries (
  run_id TEXT NOT NULL,
  asof_date DATE NOT NULL,
  hold_end DATE NOT NULL,
  n_al INT,
  port_ret NUMERIC,
  bench_ret NUMERIC,
  port_cum NUMERIC,
  bench_cum NUMERIC,
  PRIMARY KEY (run_id, asof_date)
);

CREATE INDEX IF NOT EXISTS idx_backtest_ts_run ON analytics.backtest_timeseries(run_id);
