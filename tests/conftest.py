"""
Shared pytest fixtures.

TEST DOUBLE NOTE: The LLM responses used in tests/test_processing.py are
clearly labeled test doubles. They are NOT real API call recordings.
Real recordings live in recordings/ (project root); test doubles live in
tests/fixtures/recordings/.
"""
import json
import pytest
from pathlib import Path
from src.storage import init_db

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "recordings"

# Pre-defined LLM responses for each request ID — test doubles only.
_FIXTURE_RESPONSES = {
    "R1": {
        "request_id": "R1",
        "model": "gpt-4.1-mini",
        "mode": "test-double",
        "fixture_label": "TEST-ONLY: not a real API call",
        "messages": [],
        "tool_calls": [
            {
                "call_id": "call_test_r1",
                "tool_name": "search_catalog",
                "arguments": {"query": "CAB-1"},
                "result": {"found": True, "sku": "CAB-1", "description": "USB-C cable 1 m",
                           "unit_cents": 2000, "match_type": "sku"},
            }
        ],
        "tool_results": [],
        "final_response": json.dumps({
            "status": "draft",
            "lines": [{
                "sku": "CAB-1",
                "quantity": 2,
                "source_quote": "2 individual CAB-1 cables",
                "is_unresolved": False,
                "clarification_reason": None,
            }],
            "clarification_message": None,
        }),
        "raw_response": {"test_double": True},
    },
    "R2": {
        "request_id": "R2",
        "model": "gpt-4.1-mini",
        "mode": "test-double",
        "fixture_label": "TEST-ONLY: not a real API call",
        "messages": [],
        "tool_calls": [
            {
                "call_id": "call_test_r2",
                "tool_name": "search_catalog",
                "arguments": {"query": "Moon adapter"},
                "result": {"found": False, "query": "Moon adapter",
                           "reason": "No product matched 'Moon adapter'."},
            }
        ],
        "tool_results": [],
        "final_response": json.dumps({
            "status": "needs-clarification",
            "lines": [{
                "sku": None,
                "quantity": 1,
                "source_quote": "one Moon adapter",
                "is_unresolved": True,
                "clarification_reason": "Product 'Moon adapter' was not found in the catalog.",
            }],
            "clarification_message": (
                "Could you please clarify which product you need? "
                "'Moon adapter' does not match any item in our catalog."
            ),
        }),
        "raw_response": {"test_double": True},
    },
    "R3": {
        "request_id": "R3",
        "model": "gpt-4.1-mini",
        "mode": "test-double",
        "fixture_label": "TEST-ONLY: not a real API call",
        "messages": [],
        "tool_calls": [
            {
                "call_id": "call_test_r3",
                "tool_name": "search_catalog",
                "arguments": {"query": "usual cable"},
                "result": {"found": False, "query": "usual cable",
                           "reason": "No product matched 'usual cable'."},
            }
        ],
        "tool_results": [],
        "final_response": json.dumps({
            "status": "needs-clarification",
            "lines": [{
                "sku": None,
                "quantity": None,
                "source_quote": "two boxes of the usual cable",
                "is_unresolved": True,
                "clarification_reason": (
                    "Product is ambiguous ('usual cable' does not match a specific SKU) "
                    "and box quantity cannot be converted to individual item count."
                ),
            }],
            "clarification_message": (
                "Could you please specify which cable (CAB-1 or CAB-2) "
                "and the number of individual items you need?"
            ),
        }),
        "raw_response": {"test_double": True},
    },
    "R4": {
        "request_id": "R4",
        "model": "gpt-4.1-mini",
        "mode": "test-double",
        "fixture_label": "TEST-ONLY: duplicate — LLM never called",
        "messages": [],
        "tool_calls": [],
        "tool_results": [],
        "final_response": "",
        "raw_response": {"test_double": True},
    },
    "R6": {
        "request_id": "R6",
        "model": "gpt-4.1-mini",
        "mode": "test-double",
        "fixture_label": "TEST-ONLY: not a real API call",
        "messages": [],
        "tool_calls": [
            {
                "call_id": "call_test_r6",
                "tool_name": "search_catalog",
                "arguments": {"query": "CAB-1"},
                "result": {"found": True, "sku": "CAB-1", "description": "USB-C cable 1 m",
                           "unit_cents": 2000, "match_type": "sku"},
            }
        ],
        "tool_results": [],
        "final_response": json.dumps({
            "status": "draft",
            "lines": [{
                "sku": "CAB-1",
                "quantity": 10,
                "source_quote": "10 CAB-1 cables",
                "is_unresolved": False,
                "clarification_reason": None,
            }],
            "clarification_message": None,
        }),
        "raw_response": {"test_double": True},
    },
    "R10": {
        "request_id": "R10",
        "model": "gpt-4.1-mini",
        "mode": "test-double",
        "fixture_label": "TEST-ONLY: not a real API call",
        "messages": [],
        "tool_calls": [
            {
                "call_id": "call_test_r10",
                "tool_name": "search_catalog",
                "arguments": {"query": "CAB-1"},
                "result": {"found": True, "sku": "CAB-1", "description": "USB-C cable 1 m",
                           "unit_cents": 2000, "match_type": "sku"},
            }
        ],
        "tool_results": [],
        "final_response": json.dumps({
            "status": "needs-clarification",
            "lines": [{
                "sku": "CAB-1",
                "quantity": None,
                "source_quote": "some CAB-1 cables",
                "is_unresolved": True,
                "clarification_reason": "Quantity is not specified. 'some' is not a clear positive integer.",
            }],
            "clarification_message": "Could you please tell us how many CAB-1 cables you need?",
        }),
        "raw_response": {"test_double": True},
    },
}


@pytest.fixture
def db(tmp_path):
    """Fresh test database (temp file, not :memory:, so it can be reopened)."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def mock_llm(monkeypatch):
    """
    Patch src.processing.llm_module.call_llm with test-double responses.

    TEST DOUBLE — these responses are pre-defined dicts, not real API calls.
    """
    def fake_call_llm(request_id, request_text, mode="live", model="gpt-4.1-mini", **kwargs):
        if request_id not in _FIXTURE_RESPONSES:
            raise ValueError(f"No test-double fixture for request_id={request_id!r}")
        return _FIXTURE_RESPONSES[request_id]

    import src.processing as proc_module
    monkeypatch.setattr(proc_module, "llm_module", type("FakeLLM", (), {"call_llm": staticmethod(fake_call_llm)})())
    return fake_call_llm
