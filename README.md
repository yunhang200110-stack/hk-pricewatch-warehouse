# hk-pricewatch-warehouse

PostgreSQL warehouse for Hong Kong **Consumer Council Online Price Watch**
(listed supermarket / personal-care prices and promo text).

Not official CPI. Coverage is ~2,700 monitored SKUs across 9 chains, not the
whole retail market. Prices are **listed** prices, not till receipts.

## Stack

- PostgreSQL 16 (Docker)
- Python 3.11 ingest (`etl/ingest.py`)
- Star schema: `dim_store` / `dim_product` / `fact_price_snapshot`

Grain: one fact row per `(snapshot_date, product_code, store_code)`.
Re-running the same date upserts; it does not duplicate rows.

## Quick start

```bash
docker compose up -d
# DBeaver: 127.0.0.1:5433  user/db/password: pricewatch
