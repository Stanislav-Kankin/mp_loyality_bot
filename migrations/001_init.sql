-- 001_init.sql
-- Base schema for MVP

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sellers
CREATE TABLE IF NOT EXISTS sellers (
    id BIGSERIAL PRIMARY KEY,
    tg_user_id BIGINT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Shops
CREATE TABLE IF NOT EXISTS shops (
    id BIGSERIAL PRIMARY KEY,
    seller_id BIGINT NOT NULL REFERENCES sellers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_shops_seller_id ON shops(seller_id);

-- Customers
CREATE TABLE IF NOT EXISTS customers (
    id BIGSERIAL PRIMARY KEY,
    tg_user_id BIGINT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Shop Customers (opt-in/out)
CREATE TABLE IF NOT EXISTS shop_customers (
    shop_id BIGINT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('subscribed','unsubscribed')),
    subscribed_at TIMESTAMPTZ,
    unsubscribed_at TIMESTAMPTZ,
    PRIMARY KEY (shop_id, customer_id)
);
CREATE INDEX IF NOT EXISTS idx_shop_customers_shop_status ON shop_customers(shop_id, status);

-- Campaigns
CREATE TABLE IF NOT EXISTS campaigns (
    id BIGSERIAL PRIMARY KEY,
    shop_id BIGINT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('draft','awaiting_payment','paid','sending','completed','canceled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at TIMESTAMPTZ,
    text TEXT NOT NULL,
    button_title TEXT,
    url TEXT,
    price_minor INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'RUB',
    tg_payment_charge_id TEXT,
    provider_payment_charge_id TEXT,

    total_recipients INTEGER NOT NULL DEFAULT 0,
    sent_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    click_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_campaigns_shop_status ON campaigns(shop_id, status);

-- Deliveries queue (one row per recipient)
CREATE TABLE IF NOT EXISTS campaign_deliveries (
    id BIGSERIAL PRIMARY KEY,
    campaign_id BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('pending','sent','failed','blocked')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT,
    sent_at TIMESTAMPTZ,
    tg_message_id BIGINT,

    UNIQUE (campaign_id, customer_id)
);
CREATE INDEX IF NOT EXISTS idx_deliveries_queue ON campaign_deliveries(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_deliveries_campaign ON campaign_deliveries(campaign_id);

-- Clicks (count unique customer clicks per campaign)
CREATE TABLE IF NOT EXISTS clicks (
    campaign_id BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    clicked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, customer_id)
);

-- Manual Orders
CREATE TABLE IF NOT EXISTS manual_orders (
    id BIGSERIAL PRIMARY KEY,
    shop_id BIGINT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    order_id TEXT NOT NULL,
    amount_minor BIGINT NOT NULL,
    order_date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (shop_id, order_id)
);
CREATE INDEX IF NOT EXISTS idx_manual_orders_shop_date ON manual_orders(shop_id, order_date);
