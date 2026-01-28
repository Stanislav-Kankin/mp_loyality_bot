-- 003_shop_welcome_and_campaign_media.sql
-- Add per-shop welcome message/media and campaign optional photo

ALTER TABLE shops
    ADD COLUMN IF NOT EXISTS welcome_text TEXT;

ALTER TABLE shops
    ADD COLUMN IF NOT EXISTS welcome_photo_file_id TEXT;

ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS photo_file_id TEXT;
