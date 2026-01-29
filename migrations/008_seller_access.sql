-- Seller access allowlist stored in DB (so we don't edit .env for each seller)

CREATE TABLE IF NOT EXISTS seller_access (
    tg_user_id BIGINT PRIMARY KEY,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    note TEXT,
    added_by_tg_user_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_seller_access_is_active ON seller_access(is_active);
