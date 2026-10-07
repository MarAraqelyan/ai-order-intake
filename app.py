"""
AI Order Intake & Exception Handling Platform — Streamlit UI

Run with:  python -m streamlit run app.py

Persistence: all state lives in SQLite (orders.db).
Session state is used only for UI selection (which order is displayed).
"""
import streamlit as st
from pathlib import Path
import os
import sys

# Ensure the project root is on the Python path when run from any directory.
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from src import storage, loader
from src.validation import evidence_confirms_sku
from src.processing import (process_all_requests, retry_failed_requests,
                            apply_correction_and_revalidate, mark_reviewed,
                            exception_reason_counts)

DATA_DIR = str(Path(__file__).parent / "data")
# DB_PATH can be overridden in .env (see .env.example); default is orders.db next to app.py.
DB_PATH = os.environ.get("DB_PATH") or str(Path(__file__).parent / "orders.db")

# ---- Page config ----
st.set_page_config(
    page_title="AI Order Intake",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---- Helpers ----

def cents_to_usd(cents) -> str:
    if cents is None:
        return "—"
    return f"${cents / 100:.2f}"


def status_badge(status: str) -> str:
    colors = {
        "draft": "🟢",
        "needs-clarification": "🟡",
        "duplicate": "🔵",
        "failed": "🔴",
        "reviewed": "✅",
    }
    return f"{colors.get(status, '⚪')} {status}"


def ensure_db():
    storage.init_db(DB_PATH)


# ---- Sidebar ----

st.sidebar.title("Order Intake")

mode = st.sidebar.radio(
    "Processing mode",
    options=["replay", "live"],
    index=0,
    help=(
        "**replay** — loads saved API responses (no API key needed).\n\n"
        "**live** — makes real OpenAI API calls and saves new recordings."
    ),
)

if mode == "replay":
    st.sidebar.info("REPLAY MODE — using saved recordings")
else:
    st.sidebar.warning("LIVE MODE — will call OpenAI API")

st.sidebar.divider()

if st.sidebar.button("Load Requests", use_container_width=True):
    ensure_db()
    try:
        records, file_errors = loader.load_requests_tolerant(Path(DATA_DIR))
        for record in records:
            storage.insert_request(record.request_id, record.order_ref,
                                   record.text, record.source_file, DB_PATH)
        st.session_state["last_run"] = (
            "Load Requests", mode,
            [{"status": "loaded", "request_id": r.request_id} for r in records]
            + [{"status": "failed", **e} for e in file_errors])
    except Exception as exc:
        st.sidebar.error(f"Load failed: {exc}")
    st.rerun()

if st.sidebar.button("Process All", use_container_width=True):
    ensure_db()
    with st.spinner("Processing requests…"):
        results = process_all_requests(DATA_DIR, mode=mode, db_path=DB_PATH)
    st.session_state["last_run"] = ("Process All", mode, results)
    st.rerun()

if st.sidebar.button("Retry Failed", use_container_width=True):
    ensure_db()
    with st.spinner("Retrying failed requests…"):
        results = retry_failed_requests(DATA_DIR, mode=mode, db_path=DB_PATH)
    st.session_state["last_run"] = ("Retry Failed", mode, results)
    st.rerun()

# Result of the last button press (kept in session state only for display;
# every outcome is also saved in SQLite as a processing attempt).
if "last_run" in st.session_state:
    action, run_mode, results = st.session_state["last_run"]
    failed = [r for r in results if r["status"] == "failed"]
    if not results:
        st.sidebar.info(f"{action} ({run_mode}): nothing to process.")
    else:
        st.sidebar.success(f"{action} ({run_mode}): {len(results) - len(failed)} processed, "
                           f"{len(failed)} failed.")
    for f in failed:
        st.sidebar.error(f"{f['request_id']}: {f.get('error', '?')[:300]}")

st.sidebar.divider()
st.sidebar.caption(f"DB: `{Path(DB_PATH).name}`")
model_name = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
st.sidebar.caption(f"App model: `{model_name}`")


# ---- Main area ----

ensure_db()
all_orders = storage.list_orders(db_path=DB_PATH)
counts = storage.count_orders_by_status(DB_PATH)
duplicate_requests = storage.list_duplicate_requests(db_path=DB_PATH)
changed_requests = storage.list_conflicting_requests(db_path=DB_PATH)

# Summary counts row. Order counts are per order_ref; duplicate/changed counts are per request.
ready_for_review = sum(1 for o in all_orders
                       if o["status"] == "draft" and o["review_status"] != "reviewed")
c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
c1.metric("Ready for review", ready_for_review,
          help="Orders whose draft passed all checks and has not been marked reviewed yet.")
c2.metric("Needs clarification", counts.get("needs-clarification", 0),
          help="Orders with an unresolved product or quantity, or a failed check.")
c3.metric("Processing failed", counts.get("failed", 0),
          help="Orders whose latest processing attempt failed for a technical reason (retryable).")
c4.metric("Reviewed", counts.get("reviewed", 0),
          help="Valid drafts a person explicitly marked reviewed.")
c5.metric("Distinct orders", counts.get("total", 0),
          help="Number of distinct order references (orders). Duplicate requests do not add orders.")
c6.metric("Duplicate requests", len(duplicate_requests),
          help="Requests that repeat an earlier request (same order_ref and text). They link to the existing order and create no new draft.")
c7.metric("Changed requests", len(changed_requests),
          help="Requests with an existing order_ref but different text. Not merged; the order is sent back for review.")

reasons = exception_reason_counts(DB_PATH)
if any(reasons.values()):
    with st.expander("Common exception reasons"):
        for title, key in [("Failed checks", "failed_checks"),
                           ("Unresolved line reasons (from the model)", "unresolved_reasons"),
                           ("Processing failures", "processing_errors")]:
            if reasons[key]:
                st.markdown(f"**{title}**")
                for reason, n in reasons[key]:
                    st.markdown(f"- {reason} — {n}")

st.divider()

# Two-column layout
left, right = st.columns([1, 2], gap="large")

# ---- Left column: queue ----
with left:
    st.subheader("Request queue")

    status_options = ["All", "draft", "needs-clarification", "failed", "reviewed"]
    status_filter = st.radio("Filter by status", status_options, horizontal=True, label_visibility="collapsed")

    filtered_orders = (
        all_orders if status_filter == "All"
        else [o for o in all_orders if o["status"] == status_filter or
              (status_filter == "reviewed" and o["review_status"] == "reviewed")]
    )

    if not filtered_orders:
        st.info("No orders to show. Load and process requests first.")
    else:
        for order in filtered_orders:
            ref = order["order_ref"]
            status = order["status"]
            review = order["review_status"]

            label = f"{ref} — {status_badge(status)}"
            if review == "reviewed":
                label += " ✅"

            if st.button(label, key=f"btn_{ref}", use_container_width=True):
                st.session_state["selected_order_ref"] = ref


# ---- Right column: detail ----
with right:
    selected = st.session_state.get("selected_order_ref")

    if not selected:
        st.info("Select an order from the queue to see details.")
        st.stop()

    order = storage.get_order(selected, DB_PATH)
    if not order:
        st.warning(f"Order {selected!r} not found in database.")
        st.stop()

    # Find original request text.
    all_requests = storage.list_requests(DB_PATH)
    # The original request is the first non-duplicate request for this order_ref.
    original_request = next(
        (r for r in all_requests
         if r["order_ref"] == selected and not r.get("duplicate_of")), None
    )
    original_text = original_request["text"] if original_request else ""
    request_id = original_request["id"] if original_request else "?"

    st.subheader(f"Order {selected}")
    st.caption(f"Request ID: {request_id} | Status: {status_badge(order['status'])} | Review: {order['review_status']}")

    # Duplicate requests that were linked to this order instead of creating a new draft.
    for dup in storage.list_duplicate_requests(selected, DB_PATH):
        st.info(f"Request **{dup['id']}** ({dup['source_file']}) repeats request "
                f"**{dup['duplicate_of']}** and is linked to this order. No new draft was created.")

    # Requests for this order_ref whose text differs from the original: not merged.
    for changed in storage.list_conflicting_requests(selected, DB_PATH):
        st.warning(f"Request **{changed['id']}** ({changed['source_file']}) uses this order reference "
                   f"but its text differs from request **{changed['conflicts_with']}**. It was not merged "
                   f"automatically; compare it with the draft below.\n\n> {changed['text']}")

    # Latest processing attempt: live vs replay, model id, recording, or the failure.
    attempts = storage.get_processing_attempts(request_id, DB_PATH) if original_request else []
    if attempts:
        last = attempts[-1]
        raw = last["raw_response"] if isinstance(last["raw_response"], dict) else {}
        source = ("REPLAYED from saved real-call recording" if last["mode"] == "replay"
                  else "LIVE model call" if last["mode"] == "live" else last["mode"])
        details = f"{source} · model `{last['model']}` · {last['created_at'][:19].replace('T', ' ')} UTC"
        if raw.get("recording_file"):
            details += f" · recording `{raw['recording_file']}`"
        st.caption(details)
        if last["status"] == "failed":
            st.error(f"Processing failed: {last['error_message']}")

    # Original request text
    with st.expander(f"Original request text ({original_request['source_file'] if original_request else '?'})",
                     expanded=True):
        st.text(original_text)

    lines = storage.get_order_lines(selected, DB_PATH)
    findings = storage.get_validation_findings(selected, DB_PATH)
    clarification = storage.get_latest_clarification_draft(selected, DB_PATH)
    corrections = storage.get_corrections(selected, DB_PATH)

    # Proposed lines table
    if lines:
        st.subheader("Proposed lines")
        for i, line in enumerate(lines):
            col_a, col_b, col_c, col_d, col_e = st.columns([1.5, 1, 1, 1, 1.5])
            if i == 0:
                col_a.markdown("**SKU**")
                col_b.markdown("**Qty**")
                col_c.markdown("**Subtotal**")
                col_d.markdown("**Discount**")
                col_e.markdown("**Line total**")

            sku_display = line["sku"] or "*(unresolved)*"
            qty_display = str(line["quantity"]) if line["quantity"] is not None else "*(unresolved)*"
            col_a.write(sku_display)
            col_b.write(qty_display)
            col_c.write(cents_to_usd(line["subtotal_cents"]))
            col_d.write(cents_to_usd(line["discount_cents"]))
            col_e.write(cents_to_usd(line["line_total_cents"]))

        resolved_lines = [l for l in lines if not l["is_unresolved"]]
        if resolved_lines:
            order_total = sum(l["line_total_cents"] for l in resolved_lines if l["line_total_cents"])
            label = "Order total" if not any(l["is_unresolved"] for l in lines) else "Known subtotal (some lines unresolved)"
            st.markdown(f"**{label}:** {cents_to_usd(order_total)}")
    else:
        st.write("No lines.")

    # Catalog evidence
    if lines:
        with st.expander("Catalog lookup evidence"):
            for line in lines:
                sku = line.get("sku") or "(no SKU)"
                ev = line.get("lookup_evidence") or {}
                quote = line.get("source_quote", "")
                st.markdown(f"**{sku}** — source quote: *\"{quote}\"*")
                if line.get("sku") and evidence_confirms_sku(ev, line["sku"]):
                    st.success(f"Catalog match confirmed by search_catalog ({ev.get('source')}).")
                elif line.get("sku"):
                    st.warning("Not a confirmed catalog match: no lookup found this SKU.")
                elif ev.get("searches"):
                    st.info("Product unresolved. Searches below did not identify one product.")
                else:
                    st.info("Product unresolved. No catalog search was made for this line.")
                if line.get("clarification_reason"):
                    st.caption(f"Model's reason: {line['clarification_reason']}")
                if ev.get("searches"):
                    st.json(ev["searches"])

    # Validation findings
    if findings:
        with st.expander("Validation checks", expanded=any(not f["passed"] for f in findings)):
            for f in findings:
                icon = "✅" if f["passed"] else "❌"
                st.markdown(f"{icon} **{f['check_name']}**: {f['message']}")

    # Clarification draft
    if clarification:
        st.subheader("Clarification draft")
        st.info(clarification["draft_text"])

    # Edit section (only when not yet reviewed)
    # Editing stays available; changing a reviewed order sends it back to review.
    st.subheader("Edit and review")

    if lines:
        for line in lines:
            line_id = line["id"]
            sku_key = f"sku_{line_id}"
            qty_key = f"qty_{line_id}"

            col1, col2, col3 = st.columns([2, 1, 1])
            new_sku = col1.text_input(
                f"Line {line_id} SKU",
                value=line["sku"] or "",
                key=sku_key,
                placeholder="e.g. CAB-1",
            )
            new_qty_str = col2.text_input(
                "Quantity",
                value=str(line["quantity"]) if line["quantity"] is not None else "",
                key=qty_key,
                placeholder="e.g. 3",
            )

            save_label = f"Save line {line_id}"
            if col3.button(save_label, key=f"save_{line_id}"):
                errors = []

                # Validate and save SKU change.
                if new_sku and new_sku != (line["sku"] or ""):
                    apply_correction_and_revalidate(
                        selected, line_id, "sku", new_sku, original_text, DB_PATH
                    )

                # Validate and save quantity change.
                if new_qty_str.strip():
                    try:
                        new_qty = int(new_qty_str.strip())
                        if new_qty <= 0:
                            errors.append("Quantity must be a positive integer.")
                        elif new_qty != line["quantity"]:
                            apply_correction_and_revalidate(
                                selected, line_id, "quantity", new_qty, original_text, DB_PATH
                            )
                    except ValueError:
                        errors.append(f"'{new_qty_str}' is not a valid integer quantity.")

                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    st.rerun()

    st.divider()
    is_valid_draft = (
        order["status"] == "draft" and bool(lines)
        and not any(l["is_unresolved"] for l in lines)
        and all(f["passed"] for f in findings)
    )
    if order["review_status"] == "reviewed":
        st.success("This order has been reviewed.")
    elif not is_valid_draft:
        st.caption("Mark reviewed is available once the order is a valid draft "
                   "(all lines resolved and all checks passed).")
    if st.button("✅ Mark reviewed", key="mark_reviewed", type="primary",
                 disabled=not is_valid_draft or order["review_status"] == "reviewed"):
        try:
            mark_reviewed(selected, db_path=DB_PATH)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    # Revision history
    if corrections:
        with st.expander(f"Revision history ({len(corrections)} change(s))"):
            for corr in corrections:
                field = corr["field_name"]
                prev = corr["previous_value"] or "*(none)*"
                new = corr["new_value"]
                ts = corr["timestamp"][:19].replace("T", " ")
                st.markdown(f"- `{ts}` — **{field}**: `{prev}` → `{new}` (by {corr['actor']})")
