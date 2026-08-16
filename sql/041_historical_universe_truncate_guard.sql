-- ============================================================================
-- V24-E hardening — historical universe truly append-only.
--
-- 040 blocks UPDATE and DELETE, but PostgreSQL TRUNCATE does not fire row-level
-- DELETE triggers.  Without a statement-level TRUNCATE guard, an operator with
-- table privileges could erase the complete historical universe while every
-- UPDATE/DELETE immutability test still stayed green.
--
-- Applied as a new migration: 040 is already part of the immutable migration
-- chain and is deliberately not edited in place.
-- ============================================================================

CREATE OR REPLACE FUNCTION core.universe_membership_history_reject_truncate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'core.universe_membership_history degistirilemez: TRUNCATE denendi';
END;
$$;

DROP TRIGGER IF EXISTS trg_universe_membership_history_no_truncate
  ON core.universe_membership_history;

CREATE TRIGGER trg_universe_membership_history_no_truncate
  BEFORE TRUNCATE ON core.universe_membership_history
  FOR EACH STATEMENT
  EXECUTE FUNCTION core.universe_membership_history_reject_truncate();

COMMENT ON FUNCTION core.universe_membership_history_reject_truncate() IS
  'V24-E: append-only historical universe table cannot be truncated.';
