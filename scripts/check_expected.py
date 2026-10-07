"""
Compare a processed SQLite database with the independent answer key.

The answer key is data/expected-results.json. Totals are recomputed here from the
supplied seed catalog with plain arithmetic, NOT with src/pricing.py, so a pricing
bug in the application cannot hide itself.

Usage:  python scripts/check_expected.py [path/to/orders.db]
Exit code 0 = every request matches, 1 = at least one mismatch.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = json.loads((ROOT / "data" / "expected-results.json").read_text(encoding="utf-8"))
SEED = json.loads((ROOT / "data" / "seed" / "seed.json").read_text(encoding="utf-8"))
UNIT_CENTS = {p["sku"]: p["unit_cents"] for p in SEED["catalog"]}


def independent_total(lines) -> int:
    total = 0
    for sku, qty in lines:
        subtotal = qty * UNIT_CENTS[sku]
        discount = (subtotal + 5) // 10 if qty >= 10 else 0
        total += subtotal - discount
    return total


def main() -> int:
    db_path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "orders.db")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    mismatches = 0
    for exp in EXPECTED:
        rid, ref = exp["id"], exp["order_ref"]
        attempt = db.execute("SELECT status, mode, model FROM processing_attempts "
                             "WHERE request_id = ? ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
        order = db.execute("SELECT * FROM orders WHERE order_ref = ?", (ref,)).fetchone()
        lines = db.execute("SELECT * FROM order_lines WHERE order_ref = ? ORDER BY id", (ref,)).fetchall()
        got = [(l["sku"], l["quantity"], l["line_total_cents"]) for l in lines]
        status = attempt["status"] if attempt else "not processed"

        if exp["outcome"] == "draft":
            want = [(l["sku"], l["quantity"], l["line_total_cents"]) for l in exp["lines"]]
            arithmetic_ok = independent_total([(s, q) for s, q, _ in want]) == exp["order_total_cents"]
            app_total = sum(t for _, _, t in got if t is not None)
            ok = (status == "draft" and got == want and arithmetic_ok
                  and app_total == exp["order_total_cents"])
            observed = f"{status} {got} total={app_total}"
        elif exp["outcome"] == "needs-clarification":
            ok = status == "needs-clarification" and all(l["is_unresolved"] for l in lines)
            observed = f"{status} {[(l['sku'], l['quantity'], bool(l['is_unresolved'])) for l in lines]}"
        else:  # duplicate
            # R4 must link to an earlier request of the same order (expected: O1).
            dup_of = db.execute("SELECT r.duplicate_of, o.order_ref FROM requests r "
                                "JOIN requests o ON o.id = r.duplicate_of WHERE r.id = ?",
                                (rid,)).fetchone()
            n_orders = db.execute("SELECT COUNT(*) FROM orders WHERE order_ref = ?", (ref,)).fetchone()[0]
            ok = (status == "duplicate" and n_orders == 1 and dup_of is not None
                  and dup_of[1] == exp["same_order_as"])
            observed = (f"{status} duplicate_of={dup_of[0] if dup_of else None} "
                        f"orders_with_ref={n_orders} order_status={order['status'] if order else None}")
        mismatches += not ok
        mode = f"{attempt['mode']}/{attempt['model']}" if attempt else "-"
        print(f"{'OK      ' if ok else 'MISMATCH'} {rid:4} expected={exp['outcome']:20} observed={observed}  [{mode}]")
    distinct = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    print(f"\ndistinct orders: {distinct} (expected 10)  mismatches: {mismatches}")
    return 1 if mismatches or distinct != 10 else 0


if __name__ == "__main__":
    sys.exit(main())
