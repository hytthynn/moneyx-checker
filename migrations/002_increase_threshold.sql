-- Run once before deploying the matching application code.
-- Safe to run again.
ALTER TABLE settings
  ADD COLUMN IF NOT EXISTS increase_threshold_percent NUMERIC(5, 2) NOT NULL DEFAULT 0;
