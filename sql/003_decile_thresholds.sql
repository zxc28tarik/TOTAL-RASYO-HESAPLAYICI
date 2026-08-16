CREATE TABLE IF NOT EXISTS analytics.decile_thresholds (
  sector_code   TEXT NOT NULL,
  horizon_days  INT  NOT NULL,
  bucket_count  INT  NOT NULL,
  window_end    DATE NOT NULL,
  breakpoints   NUMERIC[] NOT NULL,
  PRIMARY KEY (sector_code, horizon_days, bucket_count, window_end)
);
CREATE INDEX IF NOT EXISTS idx_decile_thresholds_lookup
  ON analytics.decile_thresholds (sector_code, horizon_days, window_end DESC);
