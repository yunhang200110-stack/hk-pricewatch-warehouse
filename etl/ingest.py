"""Load Consumer Council Online Price Watch CSV into Postgres.

Idempotent for a given snapshot_date: rerun updates in place, no duplicate rows.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ingest")

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"

HK = ZoneInfo("Asia/Hong_Kong")
DEFAULT_URL = os.environ.get(
    "PRICEWATCH_CSV_URL",
    "https://online-price-watch.consumer.org.hk/opw/opendata/pricewatch_en.csv",
)
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pricewatch:pricewatch@127.0.0.1:5433/pricewatch",
)

COLMAP = {
    "category 1": "cat1",
    "category 2": "cat2",
    "category 3": "cat3",
    "product code": "product_code",
    "brand": "brand",
    "product name": "name_en",
    "supermarket code": "store_code",
    "price": "price",
    "offers": "offers_raw",
    "貨品分類1": "cat1",
    "貨品分類2": "cat2",
    "貨品分類3": "cat3",
    "貨品編號": "product_code",
    "品牌": "brand",
    "貨品名稱": "name_en",
    "超市代號": "store_code",
    "價格": "price",
    "優惠": "offers_raw",
}

UPSERT_PRODUCT = """
INSERT INTO dim_product (
    product_code, brand, name_en, cat1, cat2, cat3, first_seen_on, last_seen_on
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (product_code) DO UPDATE SET
    brand = EXCLUDED.brand,
    name_en = EXCLUDED.name_en,
    cat1 = EXCLUDED.cat1,
    cat2 = EXCLUDED.cat2,
    cat3 = EXCLUDED.cat3,
    last_seen_on = GREATEST(dim_product.last_seen_on, EXCLUDED.last_seen_on),
    updated_at = now()
"""

UPSERT_FACT = """
INSERT INTO fact_price_snapshot (
    snapshot_date, product_code, store_code, price, offers_raw
) VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (snapshot_date, product_code, store_code) DO UPDATE SET
    price = EXCLUDED.price,
    offers_raw = EXCLUDED.offers_raw,
    loaded_at = now()
"""


def today_hk() -> date:
    return datetime.now(HK).date()


def download_csv(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("GET %s", url)
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    dest.write_bytes(r.content)
    log.info("saved %s (%s bytes)", dest, dest.stat().st_size)
    return dest


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    rename = {}
    for c in df.columns:
        key = c.lower()
        if key in COLMAP:
            rename[c] = COLMAP[key]
        elif c in COLMAP:
            rename[c] = COLMAP[c]
    df = df.rename(columns=rename)
    need = ["product_code", "store_code"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise SystemExit(f"CSV missing columns {missing}. Got: {list(df.columns)}")

    for c in [
        "cat1",
        "cat2",
        "cat3",
        "brand",
        "name_en",
        "offers_raw",
        "product_code",
        "store_code",
    ]:
        if c not in df.columns:
            df[c] = None
        df[c] = df[c].astype("string").str.strip()
        df.loc[df[c].isin(["", "<NA>", "nan", "None"]), c] = None

    df["store_code"] = df["store_code"].str.upper()
    df["price"] = pd.to_numeric(df["price"], errors="coerce")

    df = df.dropna(subset=["product_code", "store_code"])
    df = df.drop_duplicates(subset=["product_code", "store_code"], keep="last")
    return df


def known_stores(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT store_code FROM dim_store")
        return {r[0] for r in cur.fetchall()}


def start_run(conn, snapshot_date: date) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO etl_run_log (snapshot_date, status)
            VALUES (%s, 'running')
            RETURNING run_id
            """,
            (snapshot_date,),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(
    conn,
    run_id: int,
    status: str,
    n_read: int | None,
    n_up: int | None,
    err: str | None,
):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE etl_run_log
            SET finished_at = now(), status = %s, rows_read = %s,
                rows_upserted = %s, error_message = %s
            WHERE run_id = %s
            """,
            (status, n_read, n_up, err, run_id),
        )
    conn.commit()


def batched(rows: list, size: int = 1000):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def load(df: pd.DataFrame, snapshot_date: date, conn) -> tuple[int, int]:
    stores = known_stores(conn)
    unknown = sorted(set(df["store_code"].dropna()) - stores)
    if unknown:
        log.warning("skip unknown store_code (not in dim_store): %s", unknown)
        df = df[df["store_code"].isin(stores)]

    products = (
        df.sort_values("product_code")
        .drop_duplicates("product_code")[
            ["product_code", "brand", "name_en", "cat1", "cat2", "cat3"]
        ]
    )
    prod_rows = [
        (
            r.product_code,
            r.brand,
            r.name_en,
            r.cat1,
            r.cat2,
            r.cat3,
            snapshot_date,
            snapshot_date,
        )
        for r in products.itertuples(index=False)
    ]
    fact_rows = [
        (
            snapshot_date,
            r.product_code,
            r.store_code,
            None if pd.isna(r.price) else Decimal(str(r.price)),
            None if pd.isna(r.offers_raw) else r.offers_raw,
        )
        for r in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        for chunk in batched(prod_rows):
            cur.executemany(UPSERT_PRODUCT, chunk)
        for chunk in batched(fact_rows):
            cur.executemany(UPSERT_FACT, chunk)
    conn.commit()
    return len(df), len(fact_rows)


def parse_args():
    p = argparse.ArgumentParser(description="Ingest Online Price Watch into Postgres")
    p.add_argument("--date", type=date.fromisoformat, default=today_hk(), help="YYYY-MM-DD")
    p.add_argument("--file", type=Path, help="local CSV; skip download")
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    snapshot_date = args.date
    if args.file:
        path = args.file
    else:
        path = RAW_DIR / str(snapshot_date) / "pricewatch_en.csv"
        download_csv(args.url, path)

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = normalize(df)
    log.info(
        "rows after clean: %s  skus: %s  stores: %s",
        len(df),
        df["product_code"].nunique(),
        df["store_code"].nunique(),
    )

    if args.dry_run:
        log.info("dry-run, not writing DB")
        print(df.head(8).to_string(index=False))
        return 0

    run_id = None
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            run_id = start_run(conn, snapshot_date)
            n_read, n_up = load(df, snapshot_date, conn)
            finish_run(conn, run_id, "success", n_read, n_up, None)
        log.info("run_id=%s snapshot=%s upserted=%s", run_id, snapshot_date, n_up)
        return 0
    except Exception as e:
        log.exception("ingest failed")
        if run_id is not None:
            with psycopg.connect(DATABASE_URL) as conn:
                finish_run(conn, run_id, "failed", None, None, str(e)[:2000])
        return 1


if __name__ == "__main__":
    sys.exit(main())