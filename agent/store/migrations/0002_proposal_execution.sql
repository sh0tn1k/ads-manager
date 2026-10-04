-- Phase 2: execution bookkeeping for proposals.
ALTER TABLE proposals ADD COLUMN needs_submit INTEGER NOT NULL DEFAULT 0;
ALTER TABLE proposals ADD COLUMN dry_run INTEGER NOT NULL DEFAULT 0;
ALTER TABLE proposals ADD COLUMN reject_reason_code TEXT;
ALTER TABLE proposals ADD COLUMN executed_ts INTEGER;
