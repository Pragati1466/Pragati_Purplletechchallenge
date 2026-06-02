"""
seed_pos_transactions.py
=========================
Seeds real Brigade Road POS transactions directly into the database.

The API doesn't expose a POS ingest endpoint (POS correlation is internal),
so this script writes directly to the pos_transactions table via the DB.

Usage:
    python data/seed_pos_transactions.py
    python data/seed_pos_transactions.py --api-url http://localhost:8000
    python data/seed_pos_transactions.py --db-url postgresql+asyncpg://...
"""

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))


POS_CSV = Path(__file__).parent / "pos_transactions.csv"
STORE_ID = "ST1008"


async def seed_to_db(db_url: str) -> None:
    """Write POS transactions directly to PostgreSQL."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import text

    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    rows = []
    with open(POS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    async with factory() as session:
        for row in rows:
            await session.execute(text("""
                INSERT INTO pos_transactions
                    (transaction_id, store_id, timestamp, basket_value_inr)
                VALUES
                    (:txn_id, :store_id, :ts, :basket)
                ON CONFLICT (transaction_id) DO NOTHING
            """), {
                "txn_id":  row["transaction_id"],
                "store_id": row["store_id"],
                "ts":       row["timestamp"],
                "basket":   float(row["basket_value_inr"]),
            })
        await session.commit()

    await engine.dispose()
    print(f"✓ Seeded {len(rows)} POS transactions into database")


def seed_summary() -> None:
    """Print a summary of what will be seeded."""
    rows = []
    with open(POS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    total = sum(float(r["basket_value_inr"]) for r in rows)
    times = sorted(r["timestamp"] for r in rows)
    print(f"\nPOS Data Summary — Brigade Road Bangalore (ST1008)")
    print(f"  Transactions : {len(rows)}")
    print(f"  Total GMV    : ₹{total:,.2f}")
    print(f"  Time range   : {times[0][11:19]} – {times[-1][11:19]}")
    print(f"  Date         : 2026-04-10")
    print()


def main():
    parser = argparse.ArgumentParser(description="Seed POS transactions into the database")
    parser.add_argument("--db-url", default=os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://storeuser:storepass@localhost:5432/store_intelligence"
    ))
    parser.add_argument("--api-url", default=None, help="Not used; kept for run.sh compatibility")
    args = parser.parse_args()

    seed_summary()

    print(f"Connecting to: {args.db_url.split('@')[-1]}")
    asyncio.run(seed_to_db(args.db_url))


if __name__ == "__main__":
    main()
