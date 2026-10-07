"""Unit tests for src/storage.py using an in-memory SQLite database."""
import pytest
from src.storage import (
    init_db, insert_request, get_request, list_requests,
    find_duplicate_request, find_existing_order_for_ref,
    upsert_order, get_order, list_orders, update_order_review_status,
    insert_order_line, get_order_lines, delete_order_lines, update_order_line_field,
    insert_processing_attempt, insert_validation_findings, get_validation_findings,
    insert_clarification_draft, get_latest_clarification_draft,
    insert_correction, get_corrections,
)


@pytest.fixture
def db(tmp_path):
    """Fresh in-memory-equivalent DB using a temp file."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return db_path


class TestRequests:

    def test_insert_and_get(self, db):
        insert_request("R1", "O1", "Please send 2 CAB-1.", "R1.txt", db)
        r = get_request("R1", db)
        assert r["id"] == "R1"
        assert r["order_ref"] == "O1"
        assert r["text"] == "Please send 2 CAB-1."

    def test_insert_or_ignore_duplicate(self, db):
        insert_request("R1", "O1", "text", "R1.txt", db)
        insert_request("R1", "O1", "text", "R1.txt", db)  # should not raise
        assert get_request("R1", db) is not None

    def test_get_nonexistent(self, db):
        assert get_request("NONE", db) is None


class TestDuplicateDetection:

    def test_finds_duplicate(self, db):
        insert_request("R1", "O1", "Same text.", "R1.txt", db)
        r = find_duplicate_request("O1", "Same text.", db)
        assert r is not None
        assert r["id"] == "R1"

    def test_no_duplicate_different_text(self, db):
        insert_request("R1", "O1", "Different text.", "R1.txt", db)
        assert find_duplicate_request("O1", "Other text.", db) is None

    def test_no_duplicate_different_ref(self, db):
        insert_request("R1", "O1", "Same text.", "R1.txt", db)
        assert find_duplicate_request("O2", "Same text.", db) is None


class TestOrders:

    def test_upsert_creates_order(self, db):
        upsert_order("O1", "draft", db_path=db)
        o = get_order("O1", db)
        assert o["order_ref"] == "O1"
        assert o["status"] == "draft"
        assert o["review_status"] == "pending"

    def test_upsert_updates_status(self, db):
        upsert_order("O1", "draft", db_path=db)
        upsert_order("O1", "reviewed", db_path=db)
        o = get_order("O1", db)
        assert o["status"] == "reviewed"

    def test_upsert_same_order_as(self, db):
        upsert_order("O1", "draft", db_path=db)
        upsert_order("O1-dup", "duplicate", same_order_as="O1", db_path=db)
        o = get_order("O1-dup", db)
        assert o["same_order_as"] == "O1"

    def test_update_review_status(self, db):
        upsert_order("O1", "draft", db_path=db)
        update_order_review_status("O1", "reviewed", db)
        o = get_order("O1", db)
        assert o["review_status"] == "reviewed"

    def test_list_orders_filter(self, db):
        upsert_order("O1", "draft", db_path=db)
        upsert_order("O2", "needs-clarification", db_path=db)
        drafts = list_orders("draft", db)
        assert len(drafts) == 1
        assert drafts[0]["order_ref"] == "O1"


class TestOrderLines:

    def test_insert_and_get(self, db):
        upsert_order("O1", "draft", db_path=db)
        line_id = insert_order_line(
            "O1", "CAB-1", 2, 2000, 4000, 0, 4000,
            "2 CAB-1 cables", {"sku": "CAB-1"}, False, db
        )
        lines = get_order_lines("O1", db)
        assert len(lines) == 1
        assert lines[0]["sku"] == "CAB-1"
        assert lines[0]["quantity"] == 2
        assert lines[0]["line_total_cents"] == 4000
        assert lines[0]["is_unresolved"] is False
        assert isinstance(lines[0]["lookup_evidence"], dict)

    def test_unresolved_line(self, db):
        upsert_order("O1", "needs-clarification", db_path=db)
        insert_order_line("O1", None, None, None, None, None, None,
                          "some cables", None, True, db)
        lines = get_order_lines("O1", db)
        assert lines[0]["sku"] is None
        assert lines[0]["quantity"] is None
        assert lines[0]["is_unresolved"] is True

    def test_delete_lines(self, db):
        upsert_order("O1", "draft", db_path=db)
        insert_order_line("O1", "CAB-1", 2, 2000, 4000, 0, 4000, "", {}, False, db)
        delete_order_lines("O1", db)
        assert get_order_lines("O1", db) == []

    def test_update_field_sku(self, db):
        upsert_order("O1", "needs-clarification", db_path=db)
        line_id = insert_order_line("O1", None, 2, None, None, None, None, "", None, True, db)
        update_order_line_field(line_id, "sku", "CAB-1", db)
        lines = get_order_lines("O1", db)
        assert lines[0]["sku"] == "CAB-1"

    def test_update_field_quantity(self, db):
        upsert_order("O1", "needs-clarification", db_path=db)
        line_id = insert_order_line("O1", "CAB-1", None, None, None, None, None, "", None, True, db)
        update_order_line_field(line_id, "quantity", 5, db)
        lines = get_order_lines("O1", db)
        assert lines[0]["quantity"] == 5

    def test_update_field_disallowed(self, db):
        upsert_order("O1", "draft", db_path=db)
        line_id = insert_order_line("O1", "CAB-1", 1, 2000, 2000, 0, 2000, "", {}, False, db)
        with pytest.raises(ValueError, match="not allowed"):
            update_order_line_field(line_id, "unit_cents", 9999, db)


class TestValidationFindings:

    def test_insert_and_get(self, db):
        upsert_order("O1", "draft", db_path=db)
        findings = [
            {"check_name": "sku_exists", "passed": True, "message": "OK"},
            {"check_name": "qty_valid", "passed": False, "message": "qty is None"},
        ]
        insert_validation_findings("O1", findings, db)
        saved = get_validation_findings("O1", db)
        assert len(saved) == 2
        assert saved[0]["passed"] is True
        assert saved[1]["passed"] is False


class TestClarificationDrafts:

    def test_insert_and_get_latest(self, db):
        upsert_order("O1", "needs-clarification", db_path=db)
        insert_clarification_draft("O1", "Please clarify the product.", db)
        insert_clarification_draft("O1", "Please clarify quantity too.", db)
        latest = get_latest_clarification_draft("O1", db)
        assert latest["draft_text"] == "Please clarify quantity too."

    def test_no_draft(self, db):
        upsert_order("O1", "draft", db_path=db)
        assert get_latest_clarification_draft("O1", db) is None


class TestCorrections:

    def test_insert_and_get(self, db):
        upsert_order("O1", "draft", db_path=db)
        line_id = insert_order_line("O1", None, None, None, None, None, None, "", None, True, db)
        insert_correction("O1", line_id, "sku", None, "CAB-1", "human", db)
        insert_correction("O1", line_id, "quantity", None, "3", "human", db)
        corrections = get_corrections("O1", db)
        assert len(corrections) == 2
        assert corrections[0]["field_name"] == "sku"
        assert corrections[0]["new_value"] == "CAB-1"
        assert corrections[1]["field_name"] == "quantity"
