"""Unit tests for src/validation.py."""
import pytest
from src.validation import (
    check_skus_exist, check_source_quotes_present, check_quantities_valid,
    check_lookup_evidence_present, check_no_box_quantities, run_all_checks,
)
from pathlib import Path

CATALOG_PATH = Path(__file__).parent.parent / "data" / "catalog.json"


_UNSET = object()  # sentinel to distinguish "not given" from "explicitly empty"


def confirmed(sku):
    """Evidence shape produced by processing.build_lookup_evidence for a found SKU."""
    return {"confirmed": True, "source": "search_catalog",
            "searches": [{"query": sku, "result": {"found": True, "sku": sku}}]}


def resolved_line(sku="CAB-1", qty=2, quote="2 CAB-1 cables",
                  evidence=_UNSET):
    return {
        "sku": sku,
        "quantity": qty,
        "source_quote": quote,
        "lookup_evidence": confirmed(sku) if evidence is _UNSET else evidence,
        "is_unresolved": False,
    }


def unresolved_line(quote="some cables"):
    return {
        "sku": None,
        "quantity": None,
        "source_quote": quote,
        "lookup_evidence": None,
        "is_unresolved": True,
    }


class TestCheckSkusExist:

    def test_valid_sku(self):
        r = check_skus_exist([resolved_line("CAB-1")], CATALOG_PATH)
        assert r["passed"] is True

    def test_invalid_sku(self):
        r = check_skus_exist([resolved_line("MOON-1")], CATALOG_PATH)
        assert r["passed"] is False
        assert "MOON-1" in r["message"]

    def test_unresolved_lines_ignored(self):
        r = check_skus_exist([unresolved_line()], CATALOG_PATH)
        assert r["passed"] is True

    def test_mixed(self):
        lines = [resolved_line("CAB-1"), resolved_line("FAKE-99")]
        r = check_skus_exist(lines, CATALOG_PATH)
        assert r["passed"] is False


class TestCheckSourceQuotesPresent:

    def test_quote_in_text(self):
        line = resolved_line(quote="2 individual CAB-1 cables")
        r = check_source_quotes_present([line], "Please send 2 individual CAB-1 cables.")
        assert r["passed"] is True

    def test_quote_missing(self):
        line = resolved_line(quote="3 CAB-1 cables")
        r = check_source_quotes_present([line], "Please send 2 CAB-1 cables.")
        assert r["passed"] is False

    def test_empty_quote_allowed_on_unresolved_line(self):
        r = check_source_quotes_present([unresolved_line(quote="")], "any text")
        assert r["passed"] is True

    def test_empty_quote_fails_on_resolved_line(self):
        r = check_source_quotes_present([resolved_line(quote="")], "any text")
        assert r["passed"] is False


class TestCheckQuantitiesValid:

    def test_valid_qty(self):
        r = check_quantities_valid([resolved_line(qty=2)])
        assert r["passed"] is True

    def test_unresolved_null_qty_ok(self):
        r = check_quantities_valid([unresolved_line()])
        assert r["passed"] is True

    def test_null_qty_on_resolved_fails(self):
        line = resolved_line(qty=None)
        r = check_quantities_valid([line])
        assert r["passed"] is False

    def test_zero_qty_fails(self):
        line = resolved_line(qty=0)
        r = check_quantities_valid([line])
        assert r["passed"] is False

    def test_negative_qty_fails(self):
        line = resolved_line(qty=-1)
        r = check_quantities_valid([line])
        assert r["passed"] is False

    def test_bool_qty_fails(self):
        line = resolved_line(qty=True)
        r = check_quantities_valid([line])
        assert r["passed"] is False

    def test_float_qty_fails(self):
        line = resolved_line(qty=1.5)
        r = check_quantities_valid([line])
        assert r["passed"] is False


class TestCheckLookupEvidencePresent:

    def test_evidence_present(self):
        r = check_lookup_evidence_present([resolved_line(evidence=confirmed("CAB-1"))])
        assert r["passed"] is True

    def test_evidence_missing_on_resolved(self):
        line = resolved_line(evidence={})
        r = check_lookup_evidence_present([line])
        assert r["passed"] is False

    def test_evidence_for_a_different_sku_fails(self):
        r = check_lookup_evidence_present([resolved_line("CAB-2", evidence=confirmed("CAB-1"))])
        assert r["passed"] is False

    def test_unconfirmed_evidence_fails(self):
        ev = {"confirmed": False, "searches": [{"query": "cable", "result": {"found": False}}]}
        r = check_lookup_evidence_present([resolved_line(evidence=ev)])
        assert r["passed"] is False

    def test_unresolved_line_with_unconfirmed_sku_fails(self):
        """An unresolved line that still names a SKU needs evidence for it too."""
        line = dict(unresolved_line(), sku="CAB-1")
        assert check_lookup_evidence_present([line])["passed"] is False

    def test_unresolved_no_evidence_ok(self):
        r = check_lookup_evidence_present([unresolved_line()])
        assert r["passed"] is True


class TestCheckNoBoxQuantities:

    def test_no_box_word(self):
        r = check_no_box_quantities([resolved_line(qty=2)], "Please send 2 CAB-1 cables.")
        assert r["passed"] is True

    def test_box_with_unresolved_qty_ok(self):
        r = check_no_box_quantities([unresolved_line()], "Send two boxes of the usual cable.")
        assert r["passed"] is True

    def test_box_with_resolved_qty_fails(self):
        r = check_no_box_quantities([resolved_line(qty=2)], "Send two boxes of CAB-1.")
        assert r["passed"] is False

    def test_boxes_plural(self):
        r = check_no_box_quantities([resolved_line(qty=3)], "Send 3 boxes.")
        assert r["passed"] is False


class TestRunAllChecks:

    def test_all_pass(self):
        line = resolved_line("CAB-1", 2, "2 individual CAB-1 cables", confirmed("CAB-1"))
        findings = run_all_checks([line], "Please send 2 individual CAB-1 cables.", CATALOG_PATH)
        assert len(findings) == 5
        assert all(f["passed"] for f in findings)

    def test_returns_five_checks(self):
        findings = run_all_checks([unresolved_line()], "Send some cables.", CATALOG_PATH)
        assert len(findings) == 5
