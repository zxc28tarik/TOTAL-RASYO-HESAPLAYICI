-- V24-D — Historical BIST universe membership, append-only and auditable.
--
-- This table deliberately does not infer history from core.universe_stocks.
-- Every interval must come from an explicit source snapshot/evidence object.

CREATE TABLE IF NOT EXISTS core.universe_membership_history (
  ticker TEXT NOT NULL,
  valid_from DATE NOT NULL,
  valid_to DATE NULL,
  is_tradable BOOLEAN NOT NULL,
  company_name TEXT NULL,
  sector_index_code TEXT NULL,
  sector_code TEXT NULL,
  source TEXT NOT NULL,
  source_ref TEXT NULL,
  source_sha256 TEXT NOT NULL,
  row_sha256 TEXT NOT NULL,
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, valid_from),
  UNIQUE (row_sha256),
  CHECK (btrim(ticker) <> '' AND ticker = upper(btrim(ticker))),
  CHECK (valid_to IS NULL OR valid_to > valid_from),
  CHECK (btrim(source) <> ''),
  CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (row_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_universe_membership_history_dates
  ON core.universe_membership_history (valid_from, valid_to);

CREATE INDEX IF NOT EXISTS idx_universe_membership_history_ticker_dates
  ON core.universe_membership_history (ticker, valid_from, valid_to);

CREATE OR REPLACE FUNCTION core.universe_membership_history_guard_insert()
RETURNS TRIGGER AS $$
BEGIN
  -- Serialize membership inserts per ticker.  This prevents two concurrent
  -- transactions from both accepting overlapping intervals after checking an
  -- empty snapshot.
  PERFORM pg_advisory_xact_lock(
    hashtext('core.universe_membership_history'),
    hashtext(NEW.ticker)
  );

  -- Exact replay is allowed to reach ON CONFLICT DO NOTHING.
  IF EXISTS (
    SELECT 1
      FROM core.universe_membership_history e
     WHERE e.ticker = NEW.ticker
       AND e.valid_from = NEW.valid_from
       AND e.row_sha256 = NEW.row_sha256
  ) THEN
    RETURN NEW;
  END IF;

  IF EXISTS (
    SELECT 1
      FROM core.universe_membership_history e
     WHERE e.ticker = NEW.ticker
       AND daterange(e.valid_from, e.valid_to, '[)')
           && daterange(NEW.valid_from, NEW.valid_to, '[)')
  ) THEN
    RAISE EXCEPTION
      'core.universe_membership_history overlapping interval for %: [% - %)',
      NEW.ticker, NEW.valid_from, COALESCE(NEW.valid_to::text, 'infinity');
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_universe_membership_history_guard_insert
  ON core.universe_membership_history;
CREATE TRIGGER trg_universe_membership_history_guard_insert
  BEFORE INSERT ON core.universe_membership_history
  FOR EACH ROW EXECUTE FUNCTION core.universe_membership_history_guard_insert();

CREATE OR REPLACE FUNCTION core.universe_membership_history_immutable()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION
    'core.universe_membership_history degistirilemez: % denendi', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_universe_membership_history_immutable
  ON core.universe_membership_history;
CREATE TRIGGER trg_universe_membership_history_immutable
  BEFORE UPDATE OR DELETE ON core.universe_membership_history
  FOR EACH ROW EXECUTE FUNCTION core.universe_membership_history_immutable();

COMMENT ON TABLE core.universe_membership_history IS
  'Tarihsel BIST uyelik/islem-gorebilirlik araliklari. Append-only. '
  'core.universe_stocks guncel snapshotindan geriye donuk turetilmez.';
