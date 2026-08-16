DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema='core' AND table_name='universe_stocks'
  ) THEN
    UPDATE core.universe_stocks
    SET sector_code = CASE
      WHEN upper(sector_index_code) = 'XBANK' THEN 'BANK'
      WHEN upper(sector_index_code) = 'XHOLD' THEN 'HOLDING'
      WHEN upper(sector_index_code) = 'XGMYO' THEN 'GYO'
      WHEN upper(sector_index_code) = 'XUMAL' THEN 'FINANCIAL'
      ELSE 'NONFIN'
    END
    WHERE sector_code IS NULL OR sector_code = '';
  END IF;
END $$;
