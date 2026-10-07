"""
Streamlit AppTest checks for app.py (the real UI script, driven headlessly).

Setup replays the committed REAL-CALL recordings in recordings/ into a temporary
database with no API key. These are the only tests that read the real recordings;
if those recordings are re-made, the expectations below still come from the
independent answer key (data/expected-results.json), not from the recordings.
"""
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import storage
from src.processing import process_all_requests

ROOT = Path(__file__).parent.parent
APP = str(ROOT / "app.py")


@pytest.fixture
def replay_db(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")  # .env must not supply a key for replay
    db_path = str(tmp_path / "ui.db")
    storage.init_db(db_path)
    results = process_all_requests(str(ROOT / "data"), mode="replay", db_path=db_path)
    assert [r for r in results if r["status"] == "failed"] == []
    monkeypatch.setenv("DB_PATH", db_path)
    return db_path


def start() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def click(at, key):
    at.button(key=key).click().run()
    assert not at.exception, at.exception


def metrics(at):
    return {m.label: m.value for m in at.metric}


def caption(at):
    return next(c.value for c in at.caption if c.value.startswith("Request ID"))


def order_total(at):
    return [m.value for m in at.markdown if "total:" in m.value.lower()]


def set_quantity(at, value):
    qty = next(t for t in at.text_input if t.key.startswith("qty_"))
    qty.input(value).run()
    click(at, "save_" + qty.key.split("_")[1])


def test_summary_counts(replay_db):
    assert metrics(start()) == {
        "Ready for review": "7", "Needs clarification": "3", "Processing failed": "0",
        "Reviewed": "0", "Distinct orders": "10", "Duplicate requests": "1",
        "Changed requests": "0",
    }


def test_r1_replayed_draft_and_r4_link(replay_db):
    at = start()
    click(at, "btn_O1")
    assert "draft" in caption(at)
    assert order_total(at) == ["**Order total:** $40.00"]
    assert any("REPLAYED from saved real-call recording" in c.value for c in at.caption)
    assert any("Request **R4**" in i.value and "repeats request **R1**" in i.value for i in at.info)


def test_r3_unresolved_is_not_shown_as_a_match(replay_db):
    at = start()
    click(at, "btn_O3")
    assert "needs-clarification" in caption(at)
    assert order_total(at) == []                       # no price for unresolved lines
    assert not any("Catalog match confirmed" in s.value for s in at.success)
    assert any("did not identify one product" in i.value for i in at.info)


def test_r10_correction_review_and_restart(replay_db):
    at = start()
    click(at, "btn_O9")
    assert "needs-clarification" in caption(at)
    assert at.button(key="mark_reviewed").disabled

    set_quantity(at, "3")
    assert "draft" in caption(at) and "Review: pending" in caption(at)
    assert order_total(at) == ["**Order total:** $60.00"]
    click(at, "mark_reviewed")
    assert "Review: reviewed" in caption(at)

    # Restart: a new AppTest has no session state; everything comes from SQLite.
    at2 = start()
    assert metrics(at2)["Reviewed"] == "1"
    click(at2, "btn_O9")
    assert "draft" in caption(at2) and "Review: reviewed" in caption(at2)
    assert order_total(at2) == ["**Order total:** $60.00"]
    history = [m.value for m in at2.markdown if m.value.startswith("- `")]
    assert any("**quantity**: `*(none)*` → `3`" in h for h in history)
    assert any("**review_status**: `pending` → `reviewed`" in h for h in history)

    # Reprocessing every request (replay) must not overwrite the correction.
    process_all_requests(str(ROOT / "data"), mode="replay", db_path=replay_db)
    line = storage.get_order_lines("O9", replay_db)[0]
    assert (line["quantity"], line["line_total_cents"]) == (3, 6000)
    assert storage.get_order("O9", replay_db)["review_status"] == "reviewed"
    assert len(storage.list_orders(db_path=replay_db)) == 10


def test_invalid_quantity_shows_error(replay_db):
    at = start()
    click(at, "btn_O9")
    set_quantity(at, "abc")
    assert any("not a valid integer quantity" in e.value for e in at.error)
    assert "needs-clarification" in caption(at)
