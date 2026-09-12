-- Additive W1 diagnostics. Existing records stay NULL: no provenance is invented.
ALTER TABLE analytics.module_scores
  ADD COLUMN IF NOT EXISTS module_rejections JSONB;
