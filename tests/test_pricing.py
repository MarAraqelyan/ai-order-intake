"""
Unit tests for src/pricing.py.

All expected values are verified by independent hand arithmetic,
not derived from the application under test.
"""
import pytest
from src.pricing import compute_line_total, compute_order_total


class TestComputeLineTotal:

    # ------------------------------------------------------------------ #
    # Seed cases (from domain.md worked example and expected-seed-results) #
    # ------------------------------------------------------------------ #

    def test_r1_two_cab1(self):
        """R1: 2 x CAB-1 @ 2000 = 4000; no discount (qty 2 < 10)."""
        r = compute_line_total(2, 2000)
        assert r["subtotal_cents"] == 4000
        assert r["discount_cents"] == 0
        assert r["line_total_cents"] == 4000

    # ------------------------------------------------------------------ #
    # Extended fixtures — no-discount cases                               #
    # ------------------------------------------------------------------ #

    def test_r5_nine_cab1_no_discount(self):
        """R5: 9 x CAB-1 @ 2000 = 18000; no discount (boundary: 9 < 10)."""
        r = compute_line_total(9, 2000)
        assert r["subtotal_cents"] == 18000
        assert r["discount_cents"] == 0
        assert r["line_total_cents"] == 18000

    def test_r8_two_hub1(self):
        """R8: 2 x HUB-1 @ 5000 = 10000; no discount."""
        r = compute_line_total(2, 5000)
        assert r["subtotal_cents"] == 10000
        assert r["discount_cents"] == 0
        assert r["line_total_cents"] == 10000

    def test_qty_one(self):
        """Single item: 1 x 2000 = 2000; no discount."""
        r = compute_line_total(1, 2000)
        assert r["subtotal_cents"] == 2000
        assert r["discount_cents"] == 0
        assert r["line_total_cents"] == 2000

    # ------------------------------------------------------------------ #
    # Extended fixtures — discount cases (qty >= 10)                      #
    # ------------------------------------------------------------------ #

    def test_r6_ten_cab1_discount(self):
        """R6: 10 x CAB-1 @ 2000 = 20000; discount=(20000+5)//10=2000; total=18000."""
        r = compute_line_total(10, 2000)
        assert r["subtotal_cents"] == 20000
        assert r["discount_cents"] == 2000
        assert r["line_total_cents"] == 18000

    def test_r7_ten_cab2_discount(self):
        """R7: 10 x CAB-2 @ 3000 = 30000; discount=(30000+5)//10=3000; total=27000."""
        r = compute_line_total(10, 3000)
        assert r["subtotal_cents"] == 30000
        assert r["discount_cents"] == 3000
        assert r["line_total_cents"] == 27000

    def test_r9_twelve_cab1_discount(self):
        """R9: 12 x CAB-1 @ 2000 = 24000; discount=(24000+5)//10=2400; total=21600."""
        r = compute_line_total(12, 2000)
        assert r["subtotal_cents"] == 24000
        assert r["discount_cents"] == 2400
        assert r["line_total_cents"] == 21600

    # ------------------------------------------------------------------ #
    # Discount rounding — test-only synthetic inputs                      #
    # These inputs do NOT appear in the catalog; they verify the formula. #
    # ------------------------------------------------------------------ #

    def test_discount_rounding_half_up(self):
        """
        [TEST-ONLY input: unit_cents=1005 is not a real catalog price]
        qty=10, unit=1005 -> subtotal=10050; discount=(10050+5)//10=1005; total=9045.
        Verifies that halves round up.
        """
        r = compute_line_total(10, 1005)
        assert r["discount_cents"] == 1005
        assert r["line_total_cents"] == 9045

    def test_discount_rounding_round_down(self):
        """
        [TEST-ONLY input: unit_cents=1001 is not a real catalog price]
        qty=10, unit=1001 -> subtotal=10010; discount=(10010+5)//10=1001; total=9009.
        """
        r = compute_line_total(10, 1001)
        assert r["discount_cents"] == 1001
        assert r["line_total_cents"] == 9009

    # ------------------------------------------------------------------ #
    # Invalid input rejection                                             #
    # ------------------------------------------------------------------ #

    def test_rejects_bool_true(self):
        """True is a bool, not a valid integer quantity."""
        with pytest.raises(TypeError):
            compute_line_total(True, 2000)

    def test_rejects_bool_false(self):
        with pytest.raises(TypeError):
            compute_line_total(False, 2000)

    def test_rejects_zero(self):
        with pytest.raises(ValueError):
            compute_line_total(0, 2000)

    def test_rejects_negative(self):
        with pytest.raises(ValueError):
            compute_line_total(-1, 2000)

    def test_rejects_float(self):
        with pytest.raises(TypeError):
            compute_line_total(2.5, 2000)

    def test_rejects_string(self):
        with pytest.raises(TypeError):
            compute_line_total("2", 2000)

    def test_rejects_none(self):
        with pytest.raises(TypeError):
            compute_line_total(None, 2000)

    # ------------------------------------------------------------------ #
    # Return structure                                                     #
    # ------------------------------------------------------------------ #

    def test_return_keys(self):
        r = compute_line_total(1, 2000)
        assert set(r.keys()) == {"qty", "unit_cents", "subtotal_cents",
                                  "discount_cents", "line_total_cents"}

    def test_return_values_are_ints(self):
        r = compute_line_total(2, 2000)
        for key, val in r.items():
            assert isinstance(val, int), f"{key} should be int, got {type(val)}"


class TestComputeOrderTotal:

    def test_single_line(self):
        assert compute_order_total([{"line_total_cents": 4000}]) == 4000

    def test_r11_two_lines(self):
        """R11: 1 CAB-1 (2000) + 1 HUB-1 (5000) = 7000."""
        lines = [{"line_total_cents": 2000}, {"line_total_cents": 5000}]
        assert compute_order_total(lines) == 7000

    def test_empty_lines(self):
        assert compute_order_total([]) == 0

    def test_discount_lines(self):
        """10 CAB-1 (18000) + 10 CAB-2 (27000) = 45000."""
        lines = [{"line_total_cents": 18000}, {"line_total_cents": 27000}]
        assert compute_order_total(lines) == 45000
