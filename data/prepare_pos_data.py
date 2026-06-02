"""
prepare_pos_data.py
====================
Converts Brigade_Bangalore_10_April_26 CSV into:
  1. data/pos_transactions.jsonl  — one transaction per order_id (for API seeding)
  2. data/pos_transactions.csv    — standard format matching the challenge schema

Usage:
    python data/prepare_pos_data.py
"""

import csv
import json
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

SRC = Path(__file__).parent.parent / "Brigade_Bangalore_10_April_26 (1)bc6219c.csv"
OUT_JSONL = Path(__file__).parent / "pos_transactions.jsonl"
OUT_CSV   = Path(__file__).parent / "pos_transactions.csv"

STORE_ID = "ST1008"
DATE_STR = "2026-04-10"   # 10-04-2026


def parse_time(t: str) -> str:
    """Convert HH:MM:SS to ISO-8601 UTC timestamp on 2026-04-10."""
    return f"{DATE_STR}T{t}Z"


def main():
    # Aggregate line items into orders
    orders: dict = defaultdict(lambda: {
        "order_id": "",
        "store_id": STORE_ID,
        "timestamp": "",
        "basket_value_inr": 0.0,
        "items": [],
        "salesperson": "",
        "invoice_number": "",
    })

    with open(SRC, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            oid = row["order_id"].strip()
            if not oid:
                continue

            o = orders[oid]
            o["order_id"] = oid
            o["store_id"] = STORE_ID
            o["timestamp"] = parse_time(row["order_time"].strip())
            o["invoice_number"] = row["invoice_number"].strip()
            o["salesperson"] = row["salesperson_name"].strip()

            try:
                total = float(row["total_amount"]) if row["total_amount"].strip() else 0.0
            except ValueError:
                total = 0.0

            o["basket_value_inr"] += total
            o["items"].append({
                "product_name": row["product_name"].strip(),
                "brand": row["brand_name"].strip(),
                "department": row["dep_name"].strip(),
                "sub_category": row["sub_category"].strip(),
                "qty": int(row["qty"]) if row["qty"].strip() else 0,
                "gmv": float(row["GMV"]) if row["GMV"].strip() else 0.0,
                "nmv": float(row["NMV"]) if row["NMV"].strip() else 0.0,
            })

    # Write JSONL (for API seeding)
    with open(OUT_JSONL, "w") as f:
        for oid, o in sorted(orders.items(), key=lambda x: x[1]["timestamp"]):
            f.write(json.dumps({
                "transaction_id": f"TXN_{oid}",
                "store_id": o["store_id"],
                "timestamp": o["timestamp"],
                "basket_value_inr": round(o["basket_value_inr"], 2),
                "invoice_number": o["invoice_number"],
                "salesperson": o["salesperson"],
                "item_count": len(o["items"]),
                "items": o["items"],
            }) + "\n")

    # Write CSV (challenge schema: store_id, transaction_id, timestamp, basket_value_inr)
    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["store_id", "transaction_id", "timestamp", "basket_value_inr"])
        for oid, o in sorted(orders.items(), key=lambda x: x[1]["timestamp"]):
            writer.writerow([
                o["store_id"],
                f"TXN_{oid}",
                o["timestamp"],
                round(o["basket_value_inr"], 2),
            ])

    print(f"✓ {len(orders)} transactions written to:")
    print(f"  {OUT_JSONL}")
    print(f"  {OUT_CSV}")

    # Print summary
    total_gmv = sum(o["basket_value_inr"] for o in orders.values())
    times = sorted(o["timestamp"] for o in orders.values())
    print(f"\nSummary:")
    print(f"  Store: {STORE_ID} (Brigade Road, Bangalore)")
    print(f"  Date: {DATE_STR}")
    print(f"  Transactions: {len(orders)}")
    print(f"  Total basket value: ₹{total_gmv:,.2f}")
    print(f"  Time range: {times[0][11:19]} – {times[-1][11:19]}")


if __name__ == "__main__":
    main()
