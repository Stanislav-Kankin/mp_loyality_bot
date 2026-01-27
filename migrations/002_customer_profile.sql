-- 002_customer_profile.sql
-- Add minimal buyer onboarding fields

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS full_years INTEGER;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS gender TEXT;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS onboarded_at TIMESTAMPTZ;
