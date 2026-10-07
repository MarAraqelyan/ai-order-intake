"""
Validation checks applied to LLM-proposed order lines.

Each check returns a dict: {"check_name": str, "passed": bool, "message": str}

These checks catch LLM errors that the prompt alone cannot guarantee:
  - SKUs the model invented that don't exist in the catalog.
  - Source quotes that don't actually appear in the original text.
  - Quantities that slipped through as invalid types.
  - Missing lookup evidence (model bypassed the tool).
  - Box-quantity resolution (domain rule: never infer item count from boxes).
"""
from pathlib import Path
from src.catalog import load_catalog, get_product_by_sku

CATALOG_PATH = Path(__file__).parent.parent / "data" / "catalog.json"


def check_skus_exist(lines: list, catalog_path: Path = CATALOG_PATH) -> dict:
    """All resolved lines must reference a SKU that exists in the catalog."""
    catalog = load_catalog(catalog_path)
    bad = []
    for line in lines:
        if not line.get("is_unresolved") and line.get("sku"):
            if get_product_by_sku(line["sku"], catalog) is None:
                bad.append(line["sku"])
    if bad:
        return {
            "check_name": "skus_exist",
            "passed": False,
            "message": f"Unknown SKU(s) proposed: {', '.join(bad)}",
        }
    return {"check_name": "skus_exist", "passed": True, "message": "All SKUs exist in catalog."}


def check_source_quotes_present(lines: list, original_text: str) -> dict:
    """
    Every source_quote must appear verbatim (case-sensitive substring) in original_text,
    and every resolved line must have one.
    """
    missing = []
    for i, line in enumerate(lines):
        quote = line.get("source_quote") or ""
        if quote and quote not in original_text:
            missing.append(repr(quote))
        elif not quote and not line.get("is_unresolved"):
            missing.append(f"line {i+1}: no source quote")
    if missing:
        return {
            "check_name": "source_quotes_present",
            "passed": False,
            "message": f"Source quote(s) not found in original text: {'; '.join(missing)}",
        }
    return {
        "check_name": "source_quotes_present",
        "passed": True,
        "message": "All source quotes found in original text.",
    }


def check_quantities_valid(lines: list) -> dict:
    """
    Resolved lines must have a quantity that is a positive integer.
    Null quantities on unresolved lines are allowed.
    Booleans, zero, negatives, and floats are rejected.
    """
    bad = []
    for i, line in enumerate(lines):
        if line.get("is_unresolved"):
            continue  # null qty is expected on unresolved lines
        qty = line.get("quantity")
        if qty is None:
            bad.append(f"line {i+1}: quantity is null on a resolved line")
            continue
        if isinstance(qty, bool):
            bad.append(f"line {i+1}: quantity is boolean ({qty!r})")
            continue
        if not isinstance(qty, int):
            bad.append(f"line {i+1}: quantity is not an integer ({qty!r})")
            continue
        if qty <= 0:
            bad.append(f"line {i+1}: quantity must be positive, got {qty}")
    if bad:
        return {
            "check_name": "quantities_valid",
            "passed": False,
            "message": "Invalid quantity: " + "; ".join(bad),
        }
    return {"check_name": "quantities_valid", "passed": True, "message": "All quantities valid."}


def evidence_confirms_sku(evidence, sku: str) -> bool:
    """True if the saved catalog lookup evidence found exactly this SKU."""
    if not isinstance(evidence, dict) or not evidence.get("confirmed"):
        return False
    return any(s.get("result", {}).get("found") and s.get("result", {}).get("sku") == sku
               for s in evidence.get("searches", []))


def check_lookup_evidence_present(lines: list) -> dict:
    """
    Every line that names a SKU (resolved or not) must be backed by a catalog
    lookup that found exactly that SKU. Lines without a SKU need no evidence.
    """
    missing = []
    for i, line in enumerate(lines):
        sku = line.get("sku")
        if sku and not evidence_confirms_sku(line.get("lookup_evidence"), sku):
            missing.append(f"line {i+1} (sku={sku!r})")
    if missing:
        return {
            "check_name": "lookup_evidence_present",
            "passed": False,
            "message": f"No catalog lookup confirms: {', '.join(missing)}",
        }
    return {
        "check_name": "lookup_evidence_present",
        "passed": True,
        "message": "Every proposed SKU is confirmed by a catalog lookup.",
    }


def check_no_box_quantities(lines: list, original_text: str) -> dict:
    """
    If the original text mentions 'box' or 'boxes', no line may have a
    resolved (non-null) quantity — domain rule: box contents are never inferred.
    """
    text_lower = original_text.lower()
    has_box = "box" in text_lower or "boxes" in text_lower
    if not has_box:
        return {
            "check_name": "no_box_quantities",
            "passed": True,
            "message": "No box/boxes language in request.",
        }
    resolved_with_qty = [
        line for line in lines
        if not line.get("is_unresolved") and line.get("quantity") is not None
    ]
    if resolved_with_qty:
        return {
            "check_name": "no_box_quantities",
            "passed": False,
            "message": (
                "Request contains 'box/boxes' but a quantity was resolved. "
                "Box contents must not be inferred."
            ),
        }
    return {
        "check_name": "no_box_quantities",
        "passed": True,
        "message": "Box language detected; all lines correctly left unresolved.",
    }


def run_all_checks(lines: list, original_text: str,
                   catalog_path: Path = CATALOG_PATH) -> list:
    """Run all five checks in order. Returns a list of result dicts."""
    return [
        check_skus_exist(lines, catalog_path),
        check_source_quotes_present(lines, original_text),
        check_quantities_valid(lines),
        check_lookup_evidence_present(lines),
        check_no_box_quantities(lines, original_text),
    ]
