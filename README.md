# hk-pricewatch-warehouse

PostgreSQL warehouse for Hong Kong **Consumer Council Online Price Watch**
(listed supermarket / personal-care prices and promo text).

This is **not** official CPI. The feed covers ~2,700 monitored SKUs across 9
chains, not the whole retail market. Values are **listed** unit prices, not
checkout / member prices.

## Stack

- PostgreSQL 16 (Docker Compose)
- Python 3.11 ingest job (`etl/ingest.py`)
- Star schema: `dim_store` / `dim_product` / `fact_price_snapshot` / `etl_run_log`

**Grain:** one fact row per `(snapshot_date, product_code, store_code)`.  
Re-running the same date **upserts**; it does not duplicate rows.

## Quick start

```bash
docker compose up -d
```

Connect with DBeaver:

| Field    | Value        |
|----------|--------------|
| Host     | 127.0.0.1    |
| Port     | **5433**     |
| Database | pricewatch   |
| User     | pricewatch   |
| Password | pricewatch   |

Port **5433** on the host maps to 5432 in the container (avoids a local Postgres already using 5432).

Apply schema once: open `sql/migrations/001_init.sql` in DBeaver and run the whole script (Alt+X).

```bash
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Unix: `source .venv/bin/activate` and `cp .env.example .env`.

Dry-run (no DB writes), then load. Snapshot CSVs have **no date column** — you must pass `--date`.

```bash
python -m etl.ingest --file data/raw/2026-09-14/pricewatch_en.csv --date 2026-09-14 --dry-run
python -m etl.ingest --file data/raw/2026-09-14/pricewatch_en.csv --date 2026-09-14
```

Same command a second time should keep `COUNT(*)` unchanged.

## Data

- Portal: [data.gov.hk — Online Price Watch](https://data.gov.hk/en-data/dataset/cc-pricewatch-pricewatch)
- English CSV: `https://online-price-watch.consumer.org.hk/opw/opendata/pricewatch_en.csv`
- Dictionary: [PDF](https://online-price-watch.consumer.org.hk/opw/opendata/pricewatch_data_dictionary.pdf)
- IP owner: Consumer Council. **Do not commit raw daily dumps** (`data/raw/` is gitignored).
- Unknown `store_code` values are skipped (seeded `dim_store` is the allow-list).
- Null `price` means “not quoted that day”, not $0.

## Status (2026-09)

- [x] Schema + 9 seeded chains
- [x] Idempotent ingest + `etl_run_log`
- [x] Loaded 2026-09-14: **7,704** fact rows / **2,670** SKUs / 9 stores
- [ ] Multi-day history
- [ ] Analytics SQL (cheapest store, spread, offer rate, basket)
- [ ] FastAPI

## Project layout

```text
docker-compose.yml
sql/migrations/001_init.sql
etl/ingest.py
requirements.txt
.env.example          # copy to .env locally
data/raw/             # gitignored snapshots
```

## License

MIT for **code**. Source data remains under Consumer Council / data.gov.hk terms.
