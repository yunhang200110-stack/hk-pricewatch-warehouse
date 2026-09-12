-- 001_init.sql
-- Grain of fact_price_snapshot: one row per (snapshot_date, product_code, store_code)
-- Source: Consumer Council Online Price Watch (data.gov.hk)
-- Re-run safe: IF NOT EXISTS / ON CONFLICT

CREATE TABLE IF NOT EXISTS dim_store (
    store_code   text PRIMARY KEY,
    name_en      text NOT NULL,
    name_zh      text NOT NULL,
    channel      text NOT NULL,
    CONSTRAINT dim_store_channel_chk
        CHECK (channel IN ('supermarket', 'personal_care', 'food_mart'))
);

COMMENT ON TABLE dim_store IS 'Monitored retail chains in Online Price Watch.';
COMMENT ON COLUMN dim_store.store_code IS 'Code from CC data dictionary, e.g. WELLCOME.';

CREATE TABLE IF NOT EXISTS dim_product (
    product_code    text PRIMARY KEY,
    brand           text,
    name_zh         text,
    name_en         text,
    cat1            text,
    cat2            text,
    cat3            text,
    first_seen_on   date,
    last_seen_on    date,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE dim_product IS 'SKU dimension. Names may change; product_code is stable.';
COMMENT ON COLUMN dim_product.product_code IS 'CC Online Price Watch product code.';

CREATE TABLE IF NOT EXISTS fact_price_snapshot (
    snapshot_date date NOT NULL,
    product_code  text NOT NULL REFERENCES dim_product (product_code),
    store_code    text NOT NULL REFERENCES dim_store (store_code),
    price         numeric(10,2),
    offers_raw    text,
    has_offer     boolean GENERATED ALWAYS AS (
                      offers_raw IS NOT NULL AND length(btrim(offers_raw)) > 0
                  ) STORED,
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_date, product_code, store_code),
    CONSTRAINT fact_price_nonneg CHECK (price IS NULL OR price >= 0)
);

COMMENT ON TABLE fact_price_snapshot IS
    'Daily listed unit price per SKU per chain. NULL price = not quoted that day.';
COMMENT ON COLUMN fact_price_snapshot.price IS 'Listed unit price in HKD; not transaction price.';
COMMENT ON COLUMN fact_price_snapshot.offers_raw IS 'Free-text offers, slash-separated in source.';

CREATE INDEX IF NOT EXISTS fact_price_snapshot_date_idx
    ON fact_price_snapshot (snapshot_date);

CREATE INDEX IF NOT EXISTS fact_price_snapshot_product_idx
    ON fact_price_snapshot (product_code);

CREATE INDEX IF NOT EXISTS fact_price_snapshot_store_date_idx
    ON fact_price_snapshot (store_code, snapshot_date);

CREATE TABLE IF NOT EXISTS etl_run_log (
    run_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_date   date,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'success', 'failed')),
    rows_read       integer,
    rows_upserted   integer,
    error_message   text
);

INSERT INTO dim_store (store_code, name_en, name_zh, channel) VALUES
    ('WELLCOME',  'Wellcome',        '惠康',     'supermarket'),
    ('PARKNSHOP', 'PARKnSHOP',       '百佳',     'supermarket'),
    ('JASONS',    'Market Place',    'Market Place', 'supermarket'),
    ('WATSONS',   'Watsons',         '屈臣氏',   'personal_care'),
    ('MANNINGS',  'Mannings',        '萬寧',     'personal_care'),
    ('AEON',      'AEON',            'AEON',     'supermarket'),
    ('DCHFOOD',   'DCH Food Mart',   '大昌食品', 'food_mart'),
    ('SASA',      'Sasa',            '莎莎',     'personal_care'),
    ('LUNGFUNG',  'Lung Fung',       '龍豐',     'supermarket')
ON CONFLICT (store_code) DO UPDATE
SET name_en = EXCLUDED.name_en,
    name_zh = EXCLUDED.name_zh,
    channel = EXCLUDED.channel;