-- 011_demo_reminders.sql
-- DEMO funnel: reminders on day 5 and day 7 + optional feedback.

ALTER TABLE sellers
    ADD COLUMN IF NOT EXISTS trial_day5_notified_at TIMESTAMPTZ;

ALTER TABLE sellers
    ADD COLUMN IF NOT EXISTS trial_day7_notified_at TIMESTAMPTZ;

ALTER TABLE sellers
    ADD COLUMN IF NOT EXISTS trial_feedback_text TEXT;

ALTER TABLE sellers
    ADD COLUMN IF NOT EXISTS trial_feedback_received_at TIMESTAMPTZ;

ALTER TABLE sellers
    ADD COLUMN IF NOT EXISTS trial_lead_notified_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_sellers_trial_day5_notified_at ON sellers(trial_day5_notified_at);
CREATE INDEX IF NOT EXISTS idx_sellers_trial_day7_notified_at ON sellers(trial_day7_notified_at);