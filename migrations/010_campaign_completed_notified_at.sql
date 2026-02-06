-- Add completion notification marker for campaigns
ALTER TABLE campaigns
ADD COLUMN IF NOT EXISTS completed_notified_at TIMESTAMPTZ;
