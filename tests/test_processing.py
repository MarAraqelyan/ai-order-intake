"""
Integration tests for src/processing.py.

Uses test-double LLM responses (see conftest.py) — not real API calls.
Each test documents the expected behavior for the five required demo scenarios.
"""
import json

import pytest
from src.llm import LLMProcessingError
from src.loader import RequestRecord
from src.processing import (
    process_one_request,
    apply_correction_and_revalidate,
    parse_llm_response,
    compute_and_attach_pricing,
)
from src import storage
from src.catalog import load_catalog
from pathlib import Path

CATALOG_PATH = Path(__file__).parent.parent / "data" / "catalog.json"


# ---------------------------------------------------------------------------
# parse_llm_response unit tests (no DB, no LLM)
# ---------------------------------------------------------------------------

class TestParseLlmResponse:

    CAB1_LOOKUP = [{"tool_name": "search_catalog", "arguments": {"query": "CAB-1"},
                    "result": {"found": True, "sku": "CAB-1", "unit_cents": 2000}}]

    def _final(self, **line_overrides):
        line = {"sku": "CAB-1", "quantity": 2, "source_quote": "2 CAB-1",
                "is_unresolved": False, "clarification_reason": None}
        line.update(line_overrides)
        return json.dumps({"status": "draft", "lines": [line], "clarification_message": None})

    def test_parse_draft(self):
        result = parse_llm_response(self._final(), self.CAB1_LOOKUP, "2 CAB-1")
        assert result["status"] == "draft"
        assert result["lines"][0]["sku"] == "CAB-1"
        assert result["lines"][0]["quantity"] == 2
        assert result["lines"][0]["lookup_evidence"]["confirmed"] is True

    def test_sku_without_lookup_is_not_confirmed(self):
        """The model names CAB-1 but never searched for it: not a confirmed match."""
        line = parse_llm_response(self._final(), [], "2 CAB-1")["lines"][0]
        assert line["lookup_evidence"]["confirmed"] is False
        assert line["is_unresolved"] is True
        assert "not confirmed" in line["clarification_reason"]

    def test_markdown_fences_are_invalid_output(self):
        with pytest.raises(LLMProcessingError, match=r"\[invalid-output\]"):
            parse_llm_response(f"```json\n{self._final()}\n```", self.CAB1_LOOKUP, "")

    def test_raises_on_invalid_json(self):
        with pytest.raises(LLMProcessingError, match="not valid JSON"):
            parse_llm_response("this is not json", [], "")

    def test_raises_on_missing_lines(self):
        with pytest.raises(LLMProcessingError, match="lines"):
            parse_llm_response('{"status": "draft"}', [], "")

    def test_raises_on_empty_output(self):
        with pytest.raises(LLMProcessingError, match=r"\[missing-output\]"):
            parse_llm_response("", [], "")

    @pytest.mark.parametrize("bad_qty", [0, -1, 1.5, True, "2"])
    def test_rejects_invalid_quantities(self, bad_qty):
        with pytest.raises(LLMProcessingError, match=r"\[invalid-output\]"):
            parse_llm_response(self._final(quantity=bad_qty), self.CAB1_LOOKUP, "2 CAB-1")

    def test_rejects_resolved_line_without_quantity(self):
        with pytest.raises(LLMProcessingError, match=r"\[invalid-output\]"):
            parse_llm_response(self._final(quantity=None), self.CAB1_LOOKUP, "2 CAB-1")

    def test_rejects_extra_fields(self):
        with pytest.raises(LLMProcessingError, match=r"\[invalid-output\]"):
            parse_llm_response(self._final(unit_price=2000), self.CAB1_LOOKUP, "2 CAB-1")


# ---------------------------------------------------------------------------
# Scenario 1: R1 — clean draft order
# ---------------------------------------------------------------------------

class TestScenario1_R1_CleanDraft:

    def test_r1_creates_draft(self, db, mock_llm):
        """R1: 2 CAB-1 cables → draft, qty=2, line_total=4000 cents ($40.00)."""
        record = RequestRecord("R1", "O1", "Please send 2 individual CAB-1 cables.", "R1.txt")
        result = process_one_request(record, mode="test-double", db_path=db)

        assert result["status"] == "draft"
        assert result["order_ref"] == "O1"

        order = storage.get_order("O1", db)
        assert order["status"] == "draft"
        assert order["review_status"] == "pending"

        lines = storage.get_order_lines("O1", db)
        assert len(lines) == 1
        assert lines[0]["sku"] == "CAB-1"
        assert lines[0]["quantity"] == 2
        assert lines[0]["subtotal_cents"] == 4000
        assert lines[0]["discount_cents"] == 0
        assert lines[0]["line_total_cents"] == 4000
        assert lines[0]["is_unresolved"] is False

    def test_r1_source_quote_present(self, db, mock_llm):
        record = RequestRecord("R1", "O1", "Please send 2 individual CAB-1 cables.", "R1.txt")
        process_one_request(record, mode="test-double", db_path=db)
        lines = storage.get_order_lines("O1", db)
        assert "2 individual CAB-1 cables" in lines[0]["source_quote"]

    def test_r1_idempotent(self, db, mock_llm):
        """Processing the same request twice does not create duplicate orders."""
        record = RequestRecord("R1", "O1", "Please send 2 individual CAB-1 cables.", "R1.txt")
        result1 = process_one_request(record, mode="test-double", db_path=db)
        result2 = process_one_request(record, mode="test-double", db_path=db)
        assert result2.get("skipped") is True
        lines = storage.get_order_lines("O1", db)
        assert len(lines) == 1  # still only one line


# ---------------------------------------------------------------------------
# Scenario 2a: R2 — unknown product needs clarification
# ---------------------------------------------------------------------------

class TestScenario2_R2_UnknownProduct:

    def test_r2_needs_clarification(self, db, mock_llm):
        """R2: 'Moon adapter' is not in the catalog → needs-clarification."""
        record = RequestRecord("R2", "O2", "Please send one Moon adapter.", "R2.txt")
        result = process_one_request(record, mode="test-double", db_path=db)

        assert result["status"] == "needs-clarification"

        order = storage.get_order("O2", db)
        assert order["status"] == "needs-clarification"

        draft = storage.get_latest_clarification_draft("O2", db)
        assert draft is not None
        assert len(draft["draft_text"]) > 0

    def test_r2_line_is_unresolved(self, db, mock_llm):
        record = RequestRecord("R2", "O2", "Please send one Moon adapter.", "R2.txt")
        process_one_request(record, mode="test-double", db_path=db)
        lines = storage.get_order_lines("O2", db)
        assert any(line["is_unresolved"] for line in lines)


# ---------------------------------------------------------------------------
# Scenario 2b: R3 — boxes and ambiguous product
# ---------------------------------------------------------------------------

class TestScenario2b_R3_BoxAndAmbiguous:

    def test_r3_needs_clarification(self, db, mock_llm):
        """R3: 'two boxes of the usual cable' → needs-clarification (boxes + ambiguous)."""
        record = RequestRecord("R3", "O3", "Send two boxes of the usual cable.", "R3.txt")
        result = process_one_request(record, mode="test-double", db_path=db)

        assert result["status"] == "needs-clarification"
        lines = storage.get_order_lines("O3", db)
        assert all(line["is_unresolved"] for line in lines)

    def test_r3_no_quantity_resolved(self, db, mock_llm):
        """No line in R3 may have a resolved quantity (box qty rule)."""
        record = RequestRecord("R3", "O3", "Send two boxes of the usual cable.", "R3.txt")
        process_one_request(record, mode="test-double", db_path=db)
        lines = storage.get_order_lines("O3", db)
        assert all(line["quantity"] is None for line in lines)


# ---------------------------------------------------------------------------
# Scenario 3: R4 — duplicate detection
# ---------------------------------------------------------------------------

class TestScenario3_R4_Duplicate:

    def test_r4_duplicate_no_new_order(self, db, mock_llm):
        """R4 has same text and order_ref as R1. No second order must be created."""
        # Process R1 first.
        r1 = RequestRecord("R1", "O1", "Please send 2 individual CAB-1 cables.", "R1.txt")
        process_one_request(r1, mode="test-double", db_path=db)

        # Process R4 (identical text, same order_ref O1).
        r4 = RequestRecord("R4", "O1", "Please send 2 individual CAB-1 cables.", "R4.txt")
        result = process_one_request(r4, mode="test-double", db_path=db)

        assert result["status"] == "duplicate"

    def test_r4_order_lines_unchanged(self, db, mock_llm):
        """After R4, O1 still has exactly the same single line from R1."""
        r1 = RequestRecord("R1", "O1", "Please send 2 individual CAB-1 cables.", "R1.txt")
        process_one_request(r1, mode="test-double", db_path=db)
        r4 = RequestRecord("R4", "O1", "Please send 2 individual CAB-1 cables.", "R4.txt")
        process_one_request(r4, mode="test-double", db_path=db)

        lines = storage.get_order_lines("O1", db)
        assert len(lines) == 1
        assert lines[0]["line_total_cents"] == 4000

    def test_r4_repeated_processing_idempotent(self, db, mock_llm):
        """Calling process_one_request for R4 multiple times doesn't grow the DB."""
        r1 = RequestRecord("R1", "O1", "Please send 2 individual CAB-1 cables.", "R1.txt")
        process_one_request(r1, mode="test-double", db_path=db)
        r4 = RequestRecord("R4", "O1", "Please send 2 individual CAB-1 cables.", "R4.txt")
        process_one_request(r4, mode="test-double", db_path=db)
        process_one_request(r4, mode="test-double", db_path=db)

        lines = storage.get_order_lines("O1", db)
        assert len(lines) == 1

    def test_r4_keeps_o1_status_and_links_request(self, db, mock_llm):
        """Regression: R4 must not overwrite O1's draft status; R4 links to R1 instead."""
        r1 = RequestRecord("R1", "O1", "Please send 2 individual CAB-1 cables.", "R1.txt")
        process_one_request(r1, mode="test-double", db_path=db)
        r4 = RequestRecord("R4", "O1", "Please send 2 individual CAB-1 cables.", "R4.txt")
        process_one_request(r4, mode="test-double", db_path=db)
        process_one_request(r4, mode="test-double", db_path=db)

        assert storage.get_order("O1", db)["status"] == "draft"
        assert storage.get_request("R4", db)["duplicate_of"] == "R1"
        assert storage.get_request("R1", db)["duplicate_of"] is None
        assert [r["id"] for r in storage.list_duplicate_requests(db_path=db)] == ["R4"]
        assert len(storage.list_orders(db_path=db)) == 1

    def test_reprocessing_r1_after_r4_is_not_a_duplicate(self, db, mock_llm):
        """R1 is the original; reprocessing it must not mark it as a duplicate of R4."""
        r1 = RequestRecord("R1", "O1", "Please send 2 individual CAB-1 cables.", "R1.txt")
        r4 = RequestRecord("R4", "O1", "Please send 2 individual CAB-1 cables.", "R4.txt")
        process_one_request(r1, mode="test-double", db_path=db)
        process_one_request(r4, mode="test-double", db_path=db)
        result = process_one_request(r1, mode="test-double", db_path=db)

        assert result["status"] == "draft"
        assert storage.get_request("R1", db)["duplicate_of"] is None


# ---------------------------------------------------------------------------
# Scenario 4: R6 — discount boundary
# ---------------------------------------------------------------------------

class TestScenario4_R6_Discount:

    def test_r6_discount_applied(self, db, mock_llm):
        """R6: 10 x CAB-1 @ 2000 = 20000; discount=2000; total=18000 ($180.00)."""
        record = RequestRecord("R6", "O5", "Please send 10 CAB-1 cables.", "R6.txt")
        result = process_one_request(record, mode="test-double", db_path=db)

        assert result["status"] == "draft"
        lines = storage.get_order_lines("O5", db)
        assert len(lines) == 1
        assert lines[0]["subtotal_cents"] == 20000
        assert lines[0]["discount_cents"] == 2000
        assert lines[0]["line_total_cents"] == 18000


# ---------------------------------------------------------------------------
# Scenario 5: R10 — reviewer correction workflow
# ---------------------------------------------------------------------------

class TestScenario5_R10_ReviewerCorrection:

    def test_r10_starts_as_clarification(self, db, mock_llm):
        """R10: 'some CAB-1 cables' — qty missing → needs-clarification."""
        record = RequestRecord("R10", "O9", "Please send some CAB-1 cables.", "R10.txt")
        process_one_request(record, mode="test-double", db_path=db)

        order = storage.get_order("O9", db)
        assert order["status"] == "needs-clarification"
        lines = storage.get_order_lines("O9", db)
        assert lines[0]["quantity"] is None
        assert lines[0]["is_unresolved"] is True

    def test_r10_correction_sets_qty(self, db, mock_llm):
        """Reviewer sets quantity=3; order becomes draft with total=6000."""
        record = RequestRecord("R10", "O9", "Please send some CAB-1 cables.", "R10.txt")
        process_one_request(record, mode="test-double", db_path=db)

        lines = storage.get_order_lines("O9", db)
        line_id = lines[0]["id"]

        apply_correction_and_revalidate(
            "O9", line_id, "quantity", 3,
            "Please send some CAB-1 cables.", db
        )

        lines = storage.get_order_lines("O9", db)
        assert lines[0]["quantity"] == 3
        assert lines[0]["line_total_cents"] == 6000
        assert lines[0]["discount_cents"] == 0

    def test_r10_correction_order_becomes_draft(self, db, mock_llm):
        """After correction, order status becomes 'draft'."""
        record = RequestRecord("R10", "O9", "Please send some CAB-1 cables.", "R10.txt")
        process_one_request(record, mode="test-double", db_path=db)

        lines = storage.get_order_lines("O9", db)
        line_id = lines[0]["id"]

        apply_correction_and_revalidate(
            "O9", line_id, "quantity", 3,
            "Please send some CAB-1 cables.", db
        )

        order = storage.get_order("O9", db)
        assert order["status"] == "draft"

    def test_r10_correction_in_history(self, db, mock_llm):
        """Correction is saved to corrections table and visible after the call."""
        record = RequestRecord("R10", "O9", "Please send some CAB-1 cables.", "R10.txt")
        process_one_request(record, mode="test-double", db_path=db)

        lines = storage.get_order_lines("O9", db)
        line_id = lines[0]["id"]

        apply_correction_and_revalidate(
            "O9", line_id, "quantity", 3,
            "Please send some CAB-1 cables.", db
        )

        corrections = storage.get_corrections("O9", db)
        assert len(corrections) >= 1
        assert corrections[-1]["field_name"] == "quantity"
        assert corrections[-1]["new_value"] == "3"

    def test_r10_correction_persists_across_db_reconnect(self, db, mock_llm):
        """Correction survives closing and reopening the database connection."""
        record = RequestRecord("R10", "O9", "Please send some CAB-1 cables.", "R10.txt")
        process_one_request(record, mode="test-double", db_path=db)

        lines = storage.get_order_lines("O9", db)
        line_id = lines[0]["id"]

        apply_correction_and_revalidate(
            "O9", line_id, "quantity", 3,
            "Please send some CAB-1 cables.", db
        )

        # Simulate reopening: read from the same db_path (new connection).
        lines_after = storage.get_order_lines("O9", db)
        assert lines_after[0]["quantity"] == 3
        assert lines_after[0]["line_total_cents"] == 6000

        order_after = storage.get_order("O9", db)
        assert order_after["status"] == "draft"

        corrections_after = storage.get_corrections("O9", db)
        assert len(corrections_after) >= 1


# ---------------------------------------------------------------------------
# Technical failure isolation
# ---------------------------------------------------------------------------

class TestFailureIsolation:

    def test_failed_request_does_not_stop_batch(self, db, monkeypatch):
        """A processing failure for one request must not raise or stop the pipeline."""
        import src.processing as proc_module

        def always_fail(request_id, request_text, mode="live", model="gpt-4.1-mini", **kwargs):
            raise RuntimeError("Simulated API failure")

        monkeypatch.setattr(proc_module, "llm_module",
                            type("FakeLLM", (), {"call_llm": staticmethod(always_fail)})())

        record = RequestRecord("R1", "O1", "Please send 2 individual CAB-1 cables.", "R1.txt")
        result = process_one_request(record, mode="live", db_path=db)

        assert result["status"] == "failed"
        assert "error" in result
        assert "Simulated API failure" in result["error"]

    def test_failed_order_saved_to_db(self, db, monkeypatch):
        """A failed attempt is recorded in the DB."""
        import src.processing as proc_module

        def always_fail(request_id, request_text, mode="live", model="gpt-4.1-mini", **kwargs):
            raise RuntimeError("DB failure test")

        monkeypatch.setattr(proc_module, "llm_module",
                            type("FakeLLM", (), {"call_llm": staticmethod(always_fail)})())

        record = RequestRecord("R1", "O1", "Please send 2 individual CAB-1 cables.", "R1.txt")
        process_one_request(record, mode="live", db_path=db)

        order = storage.get_order("O1", db)
        assert order["status"] == "failed"


# ---------------------------------------------------------------------------
# Malformed request files do not stop the batch
# ---------------------------------------------------------------------------

class TestMalformedFiles:

    def test_bad_file_fails_alone(self, db, mock_llm, tmp_path):
        """TEST-ONLY data dir: R1 is fine, R2's file is empty, R3's file is missing."""
        (tmp_path / "requests").mkdir()
        (tmp_path / "requests" / "R1.txt").write_text(R1_TEXT, encoding="utf-8")
        (tmp_path / "requests" / "R2.txt").write_text("", encoding="utf-8")
        (tmp_path / "manifest.json").write_text(json.dumps({"requests": [
            {"request_id": "R1", "order_ref": "O1", "file": "R1.txt"},
            {"request_id": "R2", "order_ref": "O2", "file": "R2.txt"},
            {"request_id": "R3", "order_ref": "O3", "file": "R3.txt"},
        ]}), encoding="utf-8")
        from src.processing import process_all_requests
        results = {r["request_id"]: r for r in process_all_requests(str(tmp_path), "test-double", db)}

        assert results["R1"]["status"] == "draft"
        assert results["R2"]["status"] == "failed" and "[malformed-file]" in results["R2"]["error"]
        assert results["R3"]["status"] == "failed" and "[malformed-file]" in results["R3"]["error"]
        assert storage.get_order("O2", db)["status"] == "failed"
        attempts = storage.get_processing_attempts("R3", db)
        assert attempts[-1]["error_message"].startswith("[malformed-file]")


# ---------------------------------------------------------------------------
# Changed content for an existing order_ref (behavior chosen by us; see README)
# ---------------------------------------------------------------------------

R1_TEXT = "Please send 2 individual CAB-1 cables."


class TestChangedContent:

    def test_changed_text_is_flagged_not_merged(self, db, mock_llm):
        process_one_request(RequestRecord("R1", "O1", R1_TEXT, "R1.txt"), mode="test-double", db_path=db)
        changed = RequestRecord("R12", "O1", "Please send 5 CAB-2 cables.", "R12.txt")
        result = process_one_request(changed, mode="test-double", db_path=db)

        assert result["status"] == "changed-content"
        assert result["conflicts_with"] == "R1"
        # New request preserved and flagged; original order untouched.
        assert storage.get_request("R12", db)["text"] == "Please send 5 CAB-2 cables."
        assert [r["id"] for r in storage.list_conflicting_requests("O1", db)] == ["R12"]
        lines = storage.get_order_lines("O1", db)
        assert [(l["sku"], l["quantity"], l["line_total_cents"]) for l in lines] == [("CAB-1", 2, 4000)]
        assert len(storage.list_orders(db_path=db)) == 1
        # mock_llm raises for unknown ids, so reaching here also proves no model call for R12.

    def test_changed_text_sends_reviewed_order_back_to_review(self, db, mock_llm):
        from src.processing import mark_reviewed
        process_one_request(RequestRecord("R1", "O1", R1_TEXT, "R1.txt"), mode="test-double", db_path=db)
        mark_reviewed("O1", db_path=db)
        process_one_request(RequestRecord("R12", "O1", "Please send 5 CAB-2 cables.", "R12.txt"),
                            mode="test-double", db_path=db)
        assert storage.get_order("O1", db)["review_status"] == "pending"

    def test_reprocessing_changed_request_stays_flagged(self, db, mock_llm):
        process_one_request(RequestRecord("R1", "O1", R1_TEXT, "R1.txt"), mode="test-double", db_path=db)
        changed = RequestRecord("R12", "O1", "Please send 5 CAB-2 cables.", "R12.txt")
        process_one_request(changed, mode="test-double", db_path=db)
        result = process_one_request(changed, mode="test-double", db_path=db)
        assert result["status"] == "changed-content"
        assert len(storage.get_order_lines("O1", db)) == 1


# ---------------------------------------------------------------------------
# Review status is separate from validation status
# ---------------------------------------------------------------------------

class TestReviewStatus:

    def _r10_line(self, db):
        process_one_request(RequestRecord("R10", "O9", "Please send some CAB-1 cables.", "R10.txt"),
                            mode="test-double", db_path=db)
        return storage.get_order_lines("O9", db)[0]

    def test_cannot_mark_needs_clarification_reviewed(self, db, mock_llm):
        from src.processing import mark_reviewed
        self._r10_line(db)
        with pytest.raises(ValueError):
            mark_reviewed("O9", db_path=db)
        assert storage.get_order("O9", db)["review_status"] == "pending"

    def test_edit_after_review_requires_new_review(self, db, mock_llm):
        from src.processing import mark_reviewed
        line = self._r10_line(db)
        text = "Please send some CAB-1 cables."
        apply_correction_and_revalidate("O9", line["id"], "quantity", 3, text, db)
        mark_reviewed("O9", db_path=db)
        assert storage.get_order("O9", db)["review_status"] == "reviewed"

        apply_correction_and_revalidate("O9", line["id"], "quantity", 12, text, db)
        order = storage.get_order("O9", db)
        assert order["status"] == "draft"
        assert order["review_status"] == "pending"
        assert storage.get_order_lines("O9", db)[0]["line_total_cents"] == 21600
        history = [(c["field_name"], c["previous_value"], c["new_value"], c["actor"])
                   for c in storage.get_corrections("O9", db)]
        assert history == [
            ("quantity", None, "3", "human"),
            ("review_status", "pending", "reviewed", "human"),
            ("quantity", "3", "12", "human"),
            ("review_status", "reviewed", "pending", "system"),
        ]

    def test_unknown_sku_correction_clears_prices(self, db, mock_llm):
        line = self._r10_line(db)
        text = "Please send some CAB-1 cables."
        apply_correction_and_revalidate("O9", line["id"], "quantity", 3, text, db)
        apply_correction_and_revalidate("O9", line["id"], "sku", "MOON-1", text, db)
        updated = storage.get_order_lines("O9", db)[0]
        assert updated["is_unresolved"] is True
        assert updated["line_total_cents"] is None
        assert storage.get_order("O9", db)["status"] == "needs-clarification"
