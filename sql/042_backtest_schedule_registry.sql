-- ============================================================================
-- V24-F — authoritative, append-only historical backtest schedules.
--
-- Real historical values are deliberately NOT seeded here.  The tables only
-- define a provenance-preserving contract for data loaded from audited sources.
-- ============================================================================

CREATE TABLE IF NOT EXISTS core.backtest_minimum_wage_schedule (
  schedule_key       TEXT        NOT NULL,
  valid_from         DATE        NOT NULL,
  valid_to           DATE,
  net_min_wage       NUMERIC     NOT NULL,
  source             TEXT        NOT NULL,
  source_ref         TEXT,
  source_sha256      CHAR(64)    NOT NULL,
  row_sha256         CHAR(64)    NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (schedule_key, valid_from),
  CHECK (btrim(schedule_key) <> ''),
  CHECK (valid_to IS NULL OR valid_to > valid_from),
  CHECK (net_min_wage > 0 AND net_min_wage::text NOT IN ('NaN','Infinity','-Infinity')),
  CHECK (btrim(source) <> ''),
  CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (row_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_backtest_minimum_wage_schedule_range
  ON core.backtest_minimum_wage_schedule(schedule_key, valid_from, valid_to);

CREATE TABLE IF NOT EXISTS analytics.backtest_signal_cutoff_schedule (
  profile_key        TEXT        NOT NULL,
  signal_date        DATE        NOT NULL,
  cutoff_at          TIMESTAMPTZ NOT NULL,
  execution_at       TIMESTAMPTZ NOT NULL,
  source             TEXT        NOT NULL,
  source_ref         TEXT,
  source_sha256      CHAR(64)    NOT NULL,
  row_sha256         CHAR(64)    NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (profile_key, signal_date),
  CHECK (btrim(profile_key) <> ''),
  CHECK (cutoff_at < execution_at),
  CHECK ((execution_at AT TIME ZONE 'Europe/Istanbul')::date = signal_date),
  CHECK (btrim(source) <> ''),
  CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (row_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_backtest_signal_cutoff_profile_execution
  ON analytics.backtest_signal_cutoff_schedule(profile_key, signal_date, execution_at);

-- Wage intervals are half-open [valid_from, valid_to).  Exact-key replay is
-- allowed through to ON CONFLICT; any other overlap in the same schedule is not.
CREATE OR REPLACE FUNCTION core.backtest_minimum_wage_schedule_guard_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF EXISTS (
    SELECT 1
      FROM core.backtest_minimum_wage_schedule x
     WHERE x.schedule_key = NEW.schedule_key
       AND x.valid_from = NEW.valid_from
  ) THEN
    RETURN NEW;
  END IF;

  IF EXISTS (
    SELECT 1
      FROM core.backtest_minimum_wage_schedule x
     WHERE x.schedule_key = NEW.schedule_key
       AND daterange(x.valid_from, x.valid_to, '[)') &&
           daterange(NEW.valid_from, NEW.valid_to, '[)')
  ) THEN
    RAISE EXCEPTION
      'core.backtest_minimum_wage_schedule overlapping interval for %: [% - %)',
      NEW.schedule_key, NEW.valid_from, COALESCE(NEW.valid_to::text, 'infinity');
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_backtest_minimum_wage_schedule_guard_insert
  ON core.backtest_minimum_wage_schedule;
CREATE TRIGGER trg_backtest_minimum_wage_schedule_guard_insert
    BEFORE INSERT ON core.backtest_minimum_wage_schedule
    FOR EACH ROW EXECUTE FUNCTION core.backtest_minimum_wage_schedule_guard_insert();

CREATE OR REPLACE FUNCTION core.backtest_schedule_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION '% degistirilemez: % denendi', TG_TABLE_NAME, TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS trg_backtest_minimum_wage_schedule_immutable
  ON core.backtest_minimum_wage_schedule;
CREATE TRIGGER trg_backtest_minimum_wage_schedule_immutable
    BEFORE UPDATE OR DELETE ON core.backtest_minimum_wage_schedule
    FOR EACH ROW EXECUTE FUNCTION core.backtest_schedule_immutable();

DROP TRIGGER IF EXISTS trg_backtest_minimum_wage_schedule_no_truncate
  ON core.backtest_minimum_wage_schedule;
CREATE TRIGGER trg_backtest_minimum_wage_schedule_no_truncate
  BEFORE TRUNCATE ON core.backtest_minimum_wage_schedule
  FOR EACH STATEMENT EXECUTE FUNCTION core.backtest_schedule_immutable();

DROP TRIGGER IF EXISTS trg_backtest_signal_cutoff_schedule_immutable
  ON analytics.backtest_signal_cutoff_schedule;
CREATE TRIGGER trg_backtest_signal_cutoff_schedule_immutable
    BEFORE UPDATE OR DELETE ON analytics.backtest_signal_cutoff_schedule
    FOR EACH ROW EXECUTE FUNCTION core.backtest_schedule_immutable();

DROP TRIGGER IF EXISTS trg_backtest_signal_cutoff_schedule_no_truncate
  ON analytics.backtest_signal_cutoff_schedule;
CREATE TRIGGER trg_backtest_signal_cutoff_schedule_no_truncate
  BEFORE TRUNCATE ON analytics.backtest_signal_cutoff_schedule
  FOR EACH STATEMENT EXECUTE FUNCTION core.backtest_schedule_immutable();

COMMENT ON TABLE core.backtest_minimum_wage_schedule IS
  'V24-F append-only, provenance-preserving net minimum-wage intervals; contains no seeded historical values.';
COMMENT ON TABLE analytics.backtest_signal_cutoff_schedule IS
  'V24-F append-only monthly PIT cutoff/execution timestamps; cutoff_at must precede execution_at.';
