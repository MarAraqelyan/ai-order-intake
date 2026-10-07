"""
Pricing calculations for order lines.

All functions are pure: no I/O, no side effects.
Domain rules (from domain.md):
  - Quantities are positive whole numbers of individual items.
  - For a line with qty >= 10, apply 10% discount to that line only.
  - Discount rounding: (subtotal + 5) // 10  (standard half-up rounding in integer cents).
  - There are no other charges.
"""


def compute_line_total(qty: int, unit_cents: int) -> dict:
    """
    Compute pricing for a single order line.

    Returns a dict with keys:
      qty, unit_cents, subtotal_cents, discount_cents, line_total_cents

    Raises:
      TypeError  if qty is a boolean (True/False are invalid quantities).
      ValueError if qty is not a positive integer, or unit_cents <= 0.
    """
    # bool is a subclass of int in Python, so this check must come first.
    if isinstance(qty, bool):
        raise TypeError(f"qty must be a positive integer, not bool: {qty!r}")

    if not isinstance(qty, int):
        raise TypeError(f"qty must be an int, got {type(qty).__name__}: {qty!r}")

    if qty <= 0:
        raise ValueError(f"qty must be a positive integer, got {qty}")

    if not isinstance(unit_cents, int) or isinstance(unit_cents, bool):
        raise ValueError(f"unit_cents must be a positive int, got {unit_cents!r}")

    if unit_cents <= 0:
        raise ValueError(f"unit_cents must be positive, got {unit_cents}")

    subtotal = qty * unit_cents

    if qty >= 10:
        # Half-up rounding: add 5 before integer division by 10.
        discount = (subtotal + 5) // 10
    else:
        discount = 0

    return {
        "qty": qty,
        "unit_cents": unit_cents,
        "subtotal_cents": subtotal,
        "discount_cents": discount,
        "line_total_cents": subtotal - discount,
    }


def compute_order_total(lines: list) -> int:
    """
    Sum line_total_cents across all lines.

    Each dict in lines must have a 'line_total_cents' key with an int value.
    Returns total in cents as int.
    """
    return sum(line["line_total_cents"] for line in lines)
