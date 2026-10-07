"""Unit tests for src/catalog.py."""
import json
from pathlib import Path

import pytest
from src.catalog import (CATALOG_PATH, MAX_CANDIDATES, Product, get_product_by_sku,
                         load_catalog, search_catalog)

SEED_PATH = Path(__file__).parent.parent / "data" / "seed" / "seed.json"


def test_runtime_catalog_matches_supplied_seed():
    """data/catalog.json is a reformatted copy of seed.json's catalog (name -> description)."""
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))["catalog"]
    expected = [(p["sku"], p["name"], p["unit_cents"]) for p in seed]
    actual = [(p.sku, p.description, p.unit_cents) for p in load_catalog(CATALOG_PATH)]
    assert actual == expected

CATALOG = [
    Product(sku="CAB-1", description="USB-C cable 1 m", unit_cents=2000),
    Product(sku="CAB-2", description="USB-C cable 2 m", unit_cents=3000),
    Product(sku="HUB-1", description="USB hub",         unit_cents=5000),
]


class TestSearchCatalog:

    def test_exact_sku_match(self):
        r = search_catalog("CAB-1", CATALOG)
        assert r["found"] is True
        assert r["sku"] == "CAB-1"
        assert r["match_type"] == "sku"
        assert r["unit_cents"] == 2000

    def test_sku_case_insensitive(self):
        r = search_catalog("cab-1", CATALOG)
        assert r["found"] is True
        assert r["sku"] == "CAB-1"

    def test_description_substring_match_unique(self):
        """'USB hub' description has only one match."""
        r = search_catalog("USB hub", CATALOG)
        assert r["found"] is True
        assert r["sku"] == "HUB-1"
        assert r["match_type"] == "description"

    def test_description_match_case_insensitive(self):
        r = search_catalog("usb hub", CATALOG)
        assert r["found"] is True
        assert r["sku"] == "HUB-1"

    def test_ambiguous_description_returns_not_found(self):
        """'USB-C cable' matches both CAB-1 and CAB-2 — must not silently pick one."""
        r = search_catalog("USB-C cable", CATALOG)
        assert r["found"] is False
        assert "Ambiguous" in r["reason"]
        assert len(r["candidates"]) == 2

    def test_unknown_product_not_found(self):
        """Moon adapter has no match."""
        r = search_catalog("Moon adapter", CATALOG)
        assert r["found"] is False
        assert "No product" in r["reason"]

    def test_empty_query(self):
        r = search_catalog("", CATALOG)
        assert r["found"] is False

    def test_whitespace_query(self):
        r = search_catalog("   ", CATALOG)
        assert r["found"] is False

    def test_vague_cable_returns_both_cables_as_candidates(self):
        """R3 'the usual cable': suggest both cables, resolve neither."""
        r = search_catalog("usual cable", CATALOG)
        assert r["found"] is False
        assert [c["sku"] for c in r["candidates"]] == ["CAB-1", "CAB-2"]
        assert "sku" not in r

    @pytest.mark.parametrize("query, sku", [
        ("USB-C 2-meter cables", "CAB-2"),   # R7 wording
        ("USB-C 2 meter cable", "CAB-2"),    # query the model actually sent for R7
        ("2m USB-C cable", "CAB-2"),
        ("USB-C cable, 1 metre", "CAB-1"),
    ])
    def test_all_description_words_match(self, query, sku):
        r = search_catalog(query, CATALOG)
        assert r["found"] is True
        assert r["sku"] == sku
        assert r["match_type"] == "description-words"

    def test_cable_without_length_stays_ambiguous(self):
        r = search_catalog("USB-C cables", CATALOG)
        assert r["found"] is False
        assert [c["sku"] for c in r["candidates"]] == ["CAB-1", "CAB-2"]

    def test_wrong_length_is_not_resolved(self):
        """A 3 m cable is not in the catalog; must not resolve to either cable."""
        r = search_catalog("USB-C cable 3 m", CATALOG)
        assert r["found"] is False

    def test_candidate_list_is_bounded(self):
        # TEST-ONLY synthetic catalog; the supplied catalog is not changed.
        many = [Product(sku=f"T-{i}", description=f"test cable {i}", unit_cents=1)
                for i in range(20)]
        r = search_catalog("cable", many)
        assert r["found"] is False
        assert len(r["candidates"]) == MAX_CANDIDATES

    def test_unrelated_word_gets_no_candidates(self):
        r = search_catalog("Moon adapter", CATALOG)
        assert "candidates" not in r

    def test_sku_priority_over_description(self):
        """Exact SKU match is returned even if description would also match."""
        r = search_catalog("CAB-2", CATALOG)
        assert r["found"] is True
        assert r["match_type"] == "sku"
        assert r["sku"] == "CAB-2"


class TestGetProductBySku:

    def test_found(self):
        p = get_product_by_sku("HUB-1", CATALOG)
        assert p is not None
        assert p.sku == "HUB-1"
        assert p.unit_cents == 5000

    def test_not_found(self):
        p = get_product_by_sku("UNKNOWN", CATALOG)
        assert p is None

    def test_case_insensitive(self):
        p = get_product_by_sku("hub-1", CATALOG)
        assert p is not None
        assert p.sku == "HUB-1"
