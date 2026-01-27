-- 004_seller_credits.sql
-- Seller credits (number of campaigns available)

CREATE TABLE IF NOT EXISTS seller_credits (
    seller_id BIGINT PRIMARY KEY REFERENCES sellers(id) ON DELETE CASCADE,
    balance INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seller_credit_transactions (
    id BIGSERIAL PRIMARY KEY,
    seller_id BIGINT NOT NULL REFERENCES sellers(id) ON DELETE CASCADE,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    campaign_id BIGINT REFERENCES campaigns(id) ON DELETE SET NULL,
    tg_payment_charge_id TEXT,
    provider_payment_charge_id TEXT,
    invoice_payload TEXT,
    balance_after INTEGER
);

CREATE INDEX IF NOT EXISTS idx_seller_credit_tx_seller_id ON seller_credit_transactions(seller_id);
CREATE INDEX IF NOT EXISTS idx_seller_credit_tx_created_at ON seller_credit_transactions(created_at);
