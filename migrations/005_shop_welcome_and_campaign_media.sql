-- 005_shop_welcome_and_campaign_media.sql
-- Add welcome message fields to shops and optional image to campaigns
ALTER TABLE shops
    ADD COLUMN IF NOT EXISTS welcome_text TEXT;

ALTER TABLE shops
    ADD COLUMN IF NOT EXISTS welcome_photo_file_id TEXT;

ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS photo_file_id TEXT;
