-- 009_demo_trials.sql
-- DEMO funnel: store trial start timestamp for sellers.

ALTER TABLE sellers
    ADD COLUMN IF NOT EXISTS trial_started_at TIMESTAMPTZ;

ALTER TABLE sellers
    ADD COLUMN IF NOT EXISTS trial_state TEXT;

CREATE INDEX IF NOT EXISTS idx_sellers_trial_started_at ON sellers(trial_started_at);
