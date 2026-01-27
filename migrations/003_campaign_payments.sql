-- Add payment fields to campaigns (safe)
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS tg_payment_charge_id TEXT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS provider_payment_charge_id TEXT;

-- Ensure status exists and default is draft (in case old schema)
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'draft';
