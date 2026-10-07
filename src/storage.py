"""
SQLite persistence for the order intake platform.

All database interactions live here. The rest of the application
calls these functions and never writes SQL directly.

Table overview:
  requests            — original customer messages
  orders              — one row per order_ref
  order_lines         — proposed line items (may be unresolved)
  processing_attempts — one row per processing run
  validation_findings — results of the five validation checks
  clarification_drafts — draft reply text for ambiguous orders
  corrections         — history of every reviewer change
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = str(Path(__file__).parent.parent / "orders.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id          TEXT PRIMARY KEY,
    order_ref   TEXT NOT NULL,
    text        TEXT NOT NULL,
    source_file TEXT NOT NULL,
    duplicate_of TEXT,          -- request id this request repeats (same order_ref + text)
    conflicts_with TEXT,        -- request id with the same order_ref but different text
    created_at  TEXT NOT NULL,
    FOREIGN KEY (duplicate_of) REFERENCES requests(id),
    FOREIGN KEY (conflicts_with) REFERENCES requests(id)
);

CREATE TABLE IF NOT EXISTS orders (
    order_ref     TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending',
    same_order_as TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (same_order_as) REFERENCES orders(order_ref)
);

CREATE TABLE IF NOT EXISTS order_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    order_ref        TEXT NOT NULL,
    sku              TEXT,
    quantity         INTEGER,
    unit_cents       INTEGER,
    subtotal_cents   INTEGER,
    discount_cents   INTEGER,
    line_total_cents INTEGER,
    source_quote     TEXT,
    lookup_evidence  TEXT,
    is_unresolved    INTEGER NOT NULL DEFAULT 0 CHECK (is_unresolved IN (0, 1)),
    clarification_reason TEXT,
    CHECK (quantity IS NULL OR quantity > 0),
    FOREIGN KEY (order_ref) REFERENCES orders(order_ref)
);

CREATE TABLE IF NOT EXISTS processing_attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id    TEXT NOT NULL,
    order_ref     TEXT NOT NULL,
    status        TEXT NOT NULL,
    model         TEXT NOT NULL,
    mode          TEXT NOT NULL,
    error_message TEXT,
    raw_response  TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES requests(id)
);

CREATE TABLE IF NOT EXISTS validation_findings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_ref  TEXT NOT NULL,
    check_name TEXT NOT NULL,
    passed     INTEGER NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (order_ref) REFERENCES orders(order_ref)
);

CREATE TABLE IF NOT EXISTS clarification_drafts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_ref  TEXT NOT NULL,
    draft_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (order_ref) REFERENCES orders(order_ref)
);

CREATE TABLE IF NOT EXISTS corrections (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_ref      TEXT NOT NULL,
    line_id        INTEGER,
    field_name     TEXT NOT NULL,
    previous_value TEXT,
    new_value      TEXT,
    actor          TEXT NOT NULL DEFAULT 'human',
    timestamp      TEXT NOT NULL,
    FOREIGN KEY (order_ref) REFERENCES orders(order_ref),
    FOREIGN KEY (line_id)   REFERENCES order_lines(id)
);
"""

_ALLOWED_LINE_FIELDS = {"sku", "quantity"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Open a connection with row_factory=sqlite3.Row and foreign-key enforcement."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    return dict(row)


def init_db(db_path: str = DB_PATH) -> None:
    """Create all tables if they do not already exist."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        # Migration for databases created before these request columns existed.
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(requests)")]
        for column in ("duplicate_of", "conflicts_with"):
            if column not in columns:
                conn.execute(f"ALTER TABLE requests ADD COLUMN {column} TEXT REFERENCES requests(id)")
        line_columns = [row["name"] for row in conn.execute("PRAGMA table_info(order_lines)")]
        if "clarification_reason" not in line_columns:
            conn.execute("ALTER TABLE order_lines ADD COLUMN clarification_reason TEXT")


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

def insert_request(request_id: str, order_ref: str, text: str,
                   source_file: str, db_path: str = DB_PATH) -> None:
    """Insert a request row. Silently ignores duplicates (INSERT OR IGNORE)."""
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO requests (id, order_ref, text, source_file, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (request_id, order_ref, text, source_file, _now()),
        )


def get_request(request_id: str, db_path: str = DB_PATH) -> Optional[dict]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
    return _row_to_dict(row)


def list_requests(db_path: str = DB_PATH) -> list:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM requests ORDER BY id").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def find_duplicate_request(order_ref: str, text: str, db_path: str = DB_PATH,
                           exclude_request_id: Optional[str] = None) -> Optional[dict]:
    """
    Return the earliest original (non-duplicate) request with this order_ref and exact text.
    Used to detect R4 (same text + same order_ref as R1) before calling the LLM.
    exclude_request_id lets a request avoid matching itself.
    """
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM requests WHERE order_ref = ? AND text = ? "
            "AND duplicate_of IS NULL AND conflicts_with IS NULL AND id IS NOT ? "
            "ORDER BY rowid LIMIT 1",
            (order_ref, text, exclude_request_id),
        ).fetchone()
    return _row_to_dict(row)


def find_original_request(order_ref: str, exclude_request_id: Optional[str] = None,
                          db_path: str = DB_PATH) -> Optional[dict]:
    """
    Return the request that created this order: the earliest request for the
    order_ref that is neither a duplicate nor a changed-content request.
    """
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM requests WHERE order_ref = ? AND duplicate_of IS NULL "
            "AND conflicts_with IS NULL AND id IS NOT ? ORDER BY rowid LIMIT 1",
            (order_ref, exclude_request_id),
        ).fetchone()
    return _row_to_dict(row)


def mark_request_conflict(request_id: str, conflicts_with: str,
                          db_path: str = DB_PATH) -> None:
    """Flag a request whose text differs from the original request for the same order_ref."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE requests SET conflicts_with = ? WHERE id = ?",
            (conflicts_with, request_id),
        )


def list_conflicting_requests(order_ref: Optional[str] = None,
                              db_path: str = DB_PATH) -> list:
    """Return requests flagged as changed content, optionally for one order_ref."""
    with get_connection(db_path) as conn:
        if order_ref:
            rows = conn.execute(
                "SELECT * FROM requests WHERE conflicts_with IS NOT NULL AND order_ref = ? "
                "ORDER BY rowid", (order_ref,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM requests WHERE conflicts_with IS NOT NULL ORDER BY rowid"
            ).fetchall()
    return [dict(r) for r in rows]


def count_processed_requests(db_path: str = DB_PATH) -> int:
    """Requests with at least one processing attempt (any outcome, duplicates included)."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT request_id) AS cnt FROM processing_attempts"
        ).fetchone()
    return row["cnt"]


def mark_request_duplicate(request_id: str, duplicate_of: str,
                           db_path: str = DB_PATH) -> None:
    """Link a repeated request to the original request. The order itself is not changed."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE requests SET duplicate_of = ? WHERE id = ?",
            (duplicate_of, request_id),
        )


def list_duplicate_requests(order_ref: Optional[str] = None,
                            db_path: str = DB_PATH) -> list:
    """Return requests flagged as duplicates, optionally only those for one order_ref."""
    with get_connection(db_path) as conn:
        if order_ref:
            rows = conn.execute(
                "SELECT * FROM requests WHERE duplicate_of IS NOT NULL AND order_ref = ? "
                "ORDER BY rowid", (order_ref,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM requests WHERE duplicate_of IS NOT NULL ORDER BY rowid"
            ).fetchall()
    return [dict(r) for r in rows]


def find_existing_order_for_ref(order_ref: str, db_path: str = DB_PATH) -> Optional[dict]:
    """Return the orders row for this order_ref, or None if it doesn't exist yet."""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_ref = ?", (order_ref,)).fetchone()
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def upsert_order(order_ref: str, status: str, same_order_as: Optional[str] = None,
                 db_path: str = DB_PATH) -> None:
    """
    Insert a new order or update its status.
    Sets created_at on first insert; always refreshes updated_at.
    """
    now = _now()
    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT created_at FROM orders WHERE order_ref = ?", (order_ref,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE orders SET status = ?, same_order_as = ?, updated_at = ? "
                "WHERE order_ref = ?",
                (status, same_order_as, now, order_ref),
            )
        else:
            conn.execute(
                "INSERT INTO orders (order_ref, status, review_status, same_order_as, created_at, updated_at) "
                "VALUES (?, ?, 'pending', ?, ?, ?)",
                (order_ref, status, same_order_as, now, now),
            )


def update_order_status(order_ref: str, status: str, db_path: str = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE order_ref = ?",
            (status, _now(), order_ref),
        )


def update_order_review_status(order_ref: str, review_status: str,
                                db_path: str = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE orders SET review_status = ?, updated_at = ? WHERE order_ref = ?",
            (review_status, _now(), order_ref),
        )


def get_order(order_ref: str, db_path: str = DB_PATH) -> Optional[dict]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_ref = ?", (order_ref,)).fetchone()
    return _row_to_dict(row)


def list_orders(status_filter: Optional[str] = None, db_path: str = DB_PATH) -> list:
    with get_connection(db_path) as conn:
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status = ? ORDER BY order_ref", (status_filter,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM orders ORDER BY order_ref").fetchall()
    return [dict(r) for r in rows]


def count_orders_by_status(db_path: str = DB_PATH) -> dict:
    """Return a dict mapping status → count, plus a 'total' key."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM orders GROUP BY status"
        ).fetchall()
        reviewed = conn.execute(
            "SELECT COUNT(*) as cnt FROM orders WHERE review_status = 'reviewed'"
        ).fetchone()
        total = conn.execute("SELECT COUNT(*) as cnt FROM orders").fetchone()
    counts = {row["status"]: row["cnt"] for row in rows}
    counts["reviewed"] = reviewed["cnt"]
    counts["total"] = total["cnt"]
    return counts


# ---------------------------------------------------------------------------
# Order lines
# ---------------------------------------------------------------------------

def insert_order_line(order_ref: str, sku: Optional[str], quantity: Optional[int],
                      unit_cents: Optional[int], subtotal_cents: Optional[int],
                      discount_cents: Optional[int], line_total_cents: Optional[int],
                      source_quote: Optional[str], lookup_evidence: Optional[dict],
                      is_unresolved: bool, db_path: str = DB_PATH) -> int:
    """Insert an order line and return its new id."""
    evidence_json = json.dumps(lookup_evidence) if lookup_evidence is not None else None
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO order_lines
               (order_ref, sku, quantity, unit_cents, subtotal_cents, discount_cents,
                line_total_cents, source_quote, lookup_evidence, is_unresolved)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (order_ref, sku, quantity, unit_cents, subtotal_cents, discount_cents,
             line_total_cents, source_quote, evidence_json, int(is_unresolved)),
        )
        return cursor.lastrowid


def delete_order_lines(order_ref: str, db_path: str = DB_PATH) -> None:
    """Delete all lines for this order_ref. Used before re-saving on retry."""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM order_lines WHERE order_ref = ?", (order_ref,))


def get_order_lines(order_ref: str, db_path: str = DB_PATH) -> list:
    """Return all order_lines for this order_ref, with lookup_evidence parsed."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM order_lines WHERE order_ref = ? ORDER BY id", (order_ref,)
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d["lookup_evidence"]:
            try:
                d["lookup_evidence"] = json.loads(d["lookup_evidence"])
            except (json.JSONDecodeError, TypeError):
                pass
        d["is_unresolved"] = bool(d["is_unresolved"])
        result.append(d)
    return result


def update_order_line_field(line_id: int, field_name: str, new_value,
                             db_path: str = DB_PATH) -> None:
    """
    Update a single field on an order_lines row.
    Only 'sku' and 'quantity' are allowed to prevent SQL injection via column names.
    """
    if field_name not in _ALLOWED_LINE_FIELDS:
        raise ValueError(
            f"Field '{field_name}' is not allowed. Permitted fields: {_ALLOWED_LINE_FIELDS}"
        )
    with get_connection(db_path) as conn:
        conn.execute(
            f"UPDATE order_lines SET {field_name} = ? WHERE id = ?",
            (new_value, line_id),
        )


def update_order_line_pricing(line_id: int, unit_cents: Optional[int],
                               subtotal_cents: Optional[int],
                               discount_cents: Optional[int],
                               line_total_cents: Optional[int],
                               is_unresolved: bool, db_path: str = DB_PATH) -> None:
    """Update all pricing fields on a line after a reviewer correction (None = unpriced)."""
    with get_connection(db_path) as conn:
        conn.execute(
            """UPDATE order_lines
               SET unit_cents = ?, subtotal_cents = ?, discount_cents = ?,
                   line_total_cents = ?, is_unresolved = ?
               WHERE id = ?""",
            (unit_cents, subtotal_cents, discount_cents, line_total_cents,
             int(is_unresolved), line_id),
        )


def update_order_line_evidence(line_id: int, lookup_evidence: Optional[dict],
                               db_path: str = DB_PATH) -> None:
    """Replace the catalog evidence for one line (used after a reviewer SKU change)."""
    evidence_json = json.dumps(lookup_evidence) if lookup_evidence is not None else None
    with get_connection(db_path) as conn:
        conn.execute("UPDATE order_lines SET lookup_evidence = ? WHERE id = ?",
                     (evidence_json, line_id))


def update_request_text(request_id: str, text: str, db_path: str = DB_PATH) -> None:
    """Fill in the text of a request whose file was unreadable earlier.
    A request that already has text is never overwritten (originals are preserved)."""
    with get_connection(db_path) as conn:
        conn.execute("UPDATE requests SET text = ? WHERE id = ? AND text = ''",
                     (text, request_id))


def save_processed_order(request_id: str, order_ref: str, status: str, lines: list,
                         findings: list, clarification: Optional[str], model: str,
                         mode: str, raw_response: Optional[dict] = None,
                         db_path: str = DB_PATH) -> None:
    """
    Save the result of one successful processing run in a single transaction:
    order status, order lines (replacing previous lines), validation findings,
    an optional clarification draft, and the processing attempt.
    If anything fails, nothing is written.
    """
    now = _now()
    with get_connection(db_path) as conn:  # commits on success, rolls back on error
        exists = conn.execute("SELECT 1 FROM orders WHERE order_ref = ?", (order_ref,)).fetchone()
        if exists:
            conn.execute("UPDATE orders SET status = ?, updated_at = ? WHERE order_ref = ?",
                         (status, now, order_ref))
        else:
            conn.execute(
                "INSERT INTO orders (order_ref, status, review_status, same_order_as, "
                "created_at, updated_at) VALUES (?, ?, 'pending', NULL, ?, ?)",
                (order_ref, status, now, now),
            )
        conn.execute("DELETE FROM order_lines WHERE order_ref = ?", (order_ref,))
        for line in lines:
            evidence = line.get("lookup_evidence")
            conn.execute(
                """INSERT INTO order_lines
                   (order_ref, sku, quantity, unit_cents, subtotal_cents, discount_cents,
                    line_total_cents, source_quote, lookup_evidence, is_unresolved,
                    clarification_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (order_ref, line.get("sku"), line.get("quantity"), line.get("unit_cents"),
                 line.get("subtotal_cents"), line.get("discount_cents"),
                 line.get("line_total_cents"), line.get("source_quote"),
                 json.dumps(evidence) if evidence is not None else None,
                 int(bool(line.get("is_unresolved"))), line.get("clarification_reason")),
            )
        conn.execute("DELETE FROM validation_findings WHERE order_ref = ?", (order_ref,))
        conn.executemany(
            "INSERT INTO validation_findings (order_ref, check_name, passed, message, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(order_ref, f["check_name"], int(f["passed"]), f["message"], now) for f in findings],
        )
        if clarification:
            conn.execute(
                "INSERT INTO clarification_drafts (order_ref, draft_text, created_at) VALUES (?, ?, ?)",
                (order_ref, clarification, now),
            )
        conn.execute(
            """INSERT INTO processing_attempts
               (request_id, order_ref, status, model, mode, error_message, raw_response, created_at)
               VALUES (?, ?, ?, ?, ?, NULL, ?, ?)""",
            (request_id, order_ref, status, model, mode,
             json.dumps(raw_response) if raw_response is not None else None, now),
        )


# ---------------------------------------------------------------------------
# Processing attempts
# ---------------------------------------------------------------------------

def insert_processing_attempt(request_id: str, order_ref: str, status: str,
                               model: str, mode: str,
                               error_message: Optional[str] = None,
                               raw_response: Optional[dict] = None,
                               db_path: str = DB_PATH) -> int:
    raw_json = json.dumps(raw_response) if raw_response is not None else None
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO processing_attempts
               (request_id, order_ref, status, model, mode, error_message, raw_response, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (request_id, order_ref, status, model, mode, error_message, raw_json, _now()),
        )
        return cursor.lastrowid


def get_processing_attempts(request_id: str, db_path: str = DB_PATH) -> list:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM processing_attempts WHERE request_id = ? ORDER BY id",
            (request_id,),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d["raw_response"]:
            try:
                d["raw_response"] = json.loads(d["raw_response"])
            except (json.JSONDecodeError, TypeError):
                pass
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Validation findings
# ---------------------------------------------------------------------------

def insert_validation_findings(order_ref: str, findings: list,
                                db_path: str = DB_PATH) -> None:
    """Bulk-insert a list of finding dicts for this order_ref."""
    now = _now()
    with get_connection(db_path) as conn:
        conn.executemany(
            "INSERT INTO validation_findings (order_ref, check_name, passed, message, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(order_ref, f["check_name"], int(f["passed"]), f["message"], now)
             for f in findings],
        )


def get_validation_findings(order_ref: str, db_path: str = DB_PATH) -> list:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM validation_findings WHERE order_ref = ? ORDER BY id",
            (order_ref,),
        ).fetchall()
    result = [dict(r) for r in rows]
    for r in result:
        r["passed"] = bool(r["passed"])
    return result


def delete_validation_findings(order_ref: str, db_path: str = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM validation_findings WHERE order_ref = ?", (order_ref,))


# ---------------------------------------------------------------------------
# Clarification drafts
# ---------------------------------------------------------------------------

def insert_clarification_draft(order_ref: str, draft_text: str,
                                db_path: str = DB_PATH) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO clarification_drafts (order_ref, draft_text, created_at) VALUES (?, ?, ?)",
            (order_ref, draft_text, _now()),
        )
        return cursor.lastrowid


def get_latest_clarification_draft(order_ref: str, db_path: str = DB_PATH) -> Optional[dict]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM clarification_drafts WHERE order_ref = ? ORDER BY id DESC LIMIT 1",
            (order_ref,),
        ).fetchone()
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------

def insert_correction(order_ref: str, line_id: Optional[int], field_name: str,
                      previous_value: Optional[str], new_value: str,
                      actor: str = "human", db_path: str = DB_PATH) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO corrections
               (order_ref, line_id, field_name, previous_value, new_value, actor, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (order_ref, line_id, field_name, previous_value, str(new_value), actor, _now()),
        )
        return cursor.lastrowid


def get_corrections(order_ref: str, db_path: str = DB_PATH) -> list:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM corrections WHERE order_ref = ? ORDER BY timestamp",
            (order_ref,),
        ).fetchall()
    return [dict(r) for r in rows]
