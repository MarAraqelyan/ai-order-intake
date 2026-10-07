"""
Catalog loading and product search.

All functions are pure (no side effects) except search_catalog_for_tool,
which loads the catalog on each call for use as an LLM tool callback.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent / "data"
CATALOG_PATH = DATA_DIR / "catalog.json"
MAX_CANDIDATES = 5  # upper bound on candidates returned to the model


@dataclass(frozen=True)
class Product:
    sku: str
    description: str
    unit_cents: int


def load_catalog(path: Path = CATALOG_PATH) -> list:
    """Read catalog.json and return a list of Product objects."""
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    return [Product(sku=item["sku"], description=item["description"],
                    unit_cents=item["unit_cents"]) for item in items]


def get_product_by_sku(sku: str, catalog: list) -> Optional[Product]:
    """Return the Product whose sku matches exactly (case-insensitive), or None."""
    sku_upper = sku.strip().upper()
    for product in catalog:
        if product.sku.upper() == sku_upper:
            return product
    return None


def normalize_words(text: str) -> set:
    """
    Lower-case words with simple spelling normalization, used only for matching:
      "2-meter", "2 meters", "2m", "2 metre" -> "2", "m"
      plural "cables" -> "cable"
    """
    text = re.sub(r"(\d)\s*-?\s*(meters?|metres?|m)\b", r"\1 m", text.lower())
    words = set()
    for word in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text):
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        words.add(word)
    return words


def search_catalog(query: str, catalog: list) -> dict:
    """
    Search the catalog for a product by SKU or description.

    Priority:
    1. Exact SKU match (case-insensitive).
    2. Case-insensitive substring match on description.
    3. All words of exactly one description appear in the query (normalized).
    4. Otherwise not found, with candidates that share a word (never resolved).

    Returns a result dict:
      Success: {"found": True, "sku": ..., "description": ..., "unit_cents": ..., "match_type": "sku"|"description"}
      Failure: {"found": False, "query": ..., "reason": ...}

    Similarity may surface candidates for review but will never silently
    resolve an unknown product to an unrelated catalog entry.
    """
    if not isinstance(query, str) or not query.strip():
        return {"found": False, "query": query, "reason": "Query must be a non-empty string."}

    query_stripped = query.strip()
    query_upper = query_stripped.upper()
    query_lower = query_stripped.lower()

    # 1. Exact SKU match.
    for product in catalog:
        if product.sku.upper() == query_upper:
            return {
                "found": True,
                "sku": product.sku,
                "description": product.description,
                "unit_cents": product.unit_cents,
                "match_type": "sku",
            }

    # 2. Case-insensitive substring on description.
    matches = [p for p in catalog if query_lower in p.description.lower()]

    if len(matches) == 1:
        p = matches[0]
        return {
            "found": True,
            "sku": p.sku,
            "description": p.description,
            "unit_cents": p.unit_cents,
            "match_type": "description",
        }

    if len(matches) > 1:
        # Multiple matches — ambiguous, do not resolve silently.
        candidates = [{"sku": p.sku, "description": p.description}
                      for p in matches[:MAX_CANDIDATES]]
        return {
            "found": False,
            "query": query_stripped,
            "reason": f"Ambiguous: {len(matches)} products match '{query_stripped}'.",
            "candidates": candidates,
        }

    # 3. Every word of a description appears in the query, after normalizing
    #    plurals and units: "USB-C 2-meter cables" -> {usb-c, 2, m, cable}, which
    #    contains all words of "USB-C cable 2 m" (CAB-2) but not "1" (CAB-1).
    query_words = normalize_words(query_stripped)
    full = [p for p in catalog if normalize_words(p.description) <= query_words]
    if len(full) == 1:
        p = full[0]
        return {
            "found": True,
            "sku": p.sku,
            "description": p.description,
            "unit_cents": p.unit_cents,
            "match_type": "description-words",
        }
    if len(full) > 1:
        return {
            "found": False,
            "query": query_stripped,
            "reason": f"Ambiguous: {len(full)} product descriptions fit '{query_stripped}'.",
            "candidates": [{"sku": p.sku, "description": p.description}
                           for p in full[:MAX_CANDIDATES]],
        }

    # 4. No description match. Suggest (never resolve) products that share a whole
    #    word with the query, e.g. "usual cable" -> both cables. Bounded list.
    words = {w for w in query_words if len(w) >= 3}
    similar = [p for p in catalog
               if words & normalize_words(p.description)][:MAX_CANDIDATES]
    if similar:
        return {
            "found": False,
            "query": query_stripped,
            "reason": (f"No exact match for '{query_stripped}'. "
                       f"{len(similar)} product(s) share a word with it; a person must choose."),
            "candidates": [{"sku": p.sku, "description": p.description} for p in similar],
        }

    return {
        "found": False,
        "query": query_stripped,
        "reason": f"No product matched '{query_stripped}'.",
    }


def search_catalog_for_tool(query: str) -> dict:
    """
    Wrapper for use as an LLM function-tool callback.
    Loads the catalog on each call (acceptable for the small catalog size).
    """
    catalog = load_catalog()
    return search_catalog(query, catalog)
