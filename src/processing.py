"""
Orchestrate the full processing pipeline for order requests.

process_one_request() is the main entry point. It never raises —
all exceptions are caught and saved as failed processing attempts.
"""
import json
import os
from pathlib import Path
from typing import Optional

from src import catalog as catalog_module
from src import llm as llm_module
from src import storage
from src.llm import LLMProcessingError, validate_final_output
from src import validation
from src.loader import RequestRecord, load_requests_tolerant
from src.pricing import compute_line_total

DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
CATALOG_PATH = Path(__file__).parent.parent / "data" / "catalog.json"
NO_MODEL_CALL = "(no model call)"  # attempts that were decided before calling the model


# ---------------------------------------------------------------------------
# Parse LLM response
# ---------------------------------------------------------------------------

def build_lookup_evidence(sku: Optional[str], tool_calls: list) -> dict:
    """
    Evidence for one line, taken only from search_catalog calls that really ran
    (never from text the model wrote). A SKU counts as confirmed only if a search
    returned found=true for exactly that SKU.
    """
    searches = [{"query": tc.get("arguments", {}).get("query"), "result": tc.get("result", {})}
                for tc in tool_calls
                if tc.get("tool_name") == "search_catalog" and "result" in tc]
    if sku:
        matching = [s for s in searches
                    if s["result"].get("found") and s["result"].get("sku") == sku]
        return {"confirmed": bool(matching), "source": "search_catalog", "searches": matching}
    # Unresolved product: keep the searches that did not resolve, for the reviewer.
    return {"confirmed": False, "source": "search_catalog",
            "searches": [s for s in searches if not s["result"].get("found")]}


def parse_llm_response(final_response: str, tool_calls: list,
                       original_text: str) -> dict:
    """
    Validate the model's final answer (Pydantic) and attach catalog evidence.

    Raises llm.LLMProcessingError for missing/malformed/invalid output.
    A SKU without a matching successful catalog lookup is kept for transparency
    but marked unresolved, so it is never priced or shown as a confirmed match.
    """
    draft = validate_final_output(final_response)

    lines = []
    for line in draft.lines:
        evidence = build_lookup_evidence(line.sku, tool_calls)
        is_unresolved = line.is_unresolved
        reason = line.clarification_reason
        if line.sku and not evidence["confirmed"]:
            is_unresolved = True
            note = f"SKU {line.sku} was not confirmed by a catalog lookup."
            reason = f"{reason} {note}" if reason else note
        lines.append({
            "sku": line.sku,
            "quantity": line.quantity,
            "source_quote": line.source_quote,
            "lookup_evidence": evidence,
            "is_unresolved": is_unresolved,
            "clarification_reason": reason,
        })

    return {
        "status": draft.status,
        "lines": lines,
        "clarification_message": draft.clarification_message,
    }


# ---------------------------------------------------------------------------
# Compute and attach pricing
# ---------------------------------------------------------------------------

def compute_and_attach_pricing(lines: list, catalog: list) -> list:
    """
    For each resolved line, look up unit_cents and compute pricing.
    Unresolved lines get None for all price fields.
    Returns the modified list (new dicts, does not mutate input).
    """
    result = []
    for line in lines:
        line = dict(line)  # copy so we don't mutate the original
        if line.get("is_unresolved") or not line.get("sku") or line.get("quantity") is None:
            line["unit_cents"] = None
            line["subtotal_cents"] = None
            line["discount_cents"] = None
            line["line_total_cents"] = None
        else:
            product = catalog_module.get_product_by_sku(line["sku"], catalog)
            if product is None:
                # SKU not found — treat as unresolved.
                line["is_unresolved"] = True
                line["unit_cents"] = None
                line["subtotal_cents"] = None
                line["discount_cents"] = None
                line["line_total_cents"] = None
            else:
                pricing = compute_line_total(line["quantity"], product.unit_cents)
                line["unit_cents"] = product.unit_cents
                line["subtotal_cents"] = pricing["subtotal_cents"]
                line["discount_cents"] = pricing["discount_cents"]
                line["line_total_cents"] = pricing["line_total_cents"]
        result.append(line)
    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_one_request(record: RequestRecord, mode: str = "live",
                         db_path: str = storage.DB_PATH) -> dict:
    """
    Full processing pipeline for one request. Never raises.

    Returns a summary dict with at least:
      {status, request_id, order_ref, error (if failed)}
    """
    model = DEFAULT_MODEL
    try:
        # 1. Save the request (idempotent).
        storage.insert_request(record.request_id, record.order_ref,
                               record.text, record.source_file, db_path)

        # 2. Duplicate check: same order_ref + identical text as an earlier request.
        #    The duplicate is recorded on the *request* (R4 -> R1); the existing
        #    order keeps its own status, lines, and any human corrections.
        existing_request = storage.find_duplicate_request(
            record.order_ref, record.text, db_path,
            exclude_request_id=record.request_id,
        )
        if existing_request:
            storage.mark_request_duplicate(record.request_id, existing_request["id"], db_path)
            storage.insert_processing_attempt(
                record.request_id, record.order_ref, "duplicate",
                NO_MODEL_CALL, mode, db_path=db_path
            )
            return {
                "status": "duplicate",
                "request_id": record.request_id,
                "order_ref": record.order_ref,
                "duplicate_of": existing_request["id"],
            }

        # 2b. Changed content: same order_ref as an earlier request but different text.
        #     The domain rules do not say how to merge two versions of an order, so we
        #     keep the new request, leave the existing order lines untouched, skip the
        #     model call, and send the order back to a person for review.
        original_request = storage.find_original_request(
            record.order_ref, exclude_request_id=record.request_id, db_path=db_path
        )
        if original_request and original_request["text"] != record.text:
            storage.mark_request_conflict(record.request_id, original_request["id"], db_path)
            if storage.find_existing_order_for_ref(record.order_ref, db_path):
                storage.update_order_review_status(record.order_ref, "pending", db_path)
            storage.insert_processing_attempt(
                record.request_id, record.order_ref, "changed-content",
                NO_MODEL_CALL, mode, db_path=db_path
            )
            return {
                "status": "changed-content",
                "request_id": record.request_id,
                "order_ref": record.order_ref,
                "conflicts_with": original_request["id"],
            }

        # 3. Skip if order already processed and not failed.
        existing_order = storage.find_existing_order_for_ref(record.order_ref, db_path)
        if existing_order and existing_order["status"] not in ("failed",):
            return {
                "status": existing_order["status"],
                "request_id": record.request_id,
                "order_ref": record.order_ref,
                "skipped": True,
            }

        # 4. Call the LLM.
        llm_result = llm_module.call_llm(record.request_id, record.text, mode, model,
                                         order_ref=record.order_ref)

        # 5. Parse the response.
        parsed = parse_llm_response(
            llm_result["final_response"],
            llm_result["tool_calls"],
            record.text,
        )

        # 6. Compute pricing for resolved lines.
        catalog = catalog_module.load_catalog(CATALOG_PATH)
        lines_with_pricing = compute_and_attach_pricing(parsed["lines"], catalog)

        # 7. Run validation checks.
        findings = validation.run_all_checks(lines_with_pricing, record.text, CATALOG_PATH)

        # 8. Determine final status. A draft is ready for review only if every line is
        #    resolved AND every check passed; otherwise a person must clarify it.
        has_unresolved = any(line.get("is_unresolved") for line in lines_with_pricing)
        all_checks_pass = all(f["passed"] for f in findings)
        final_status = "draft" if (not has_unresolved and all_checks_pass) else "needs-clarification"

        # 9-12. Save order, lines, findings, clarification draft, and the attempt in
        #       one transaction, so a crash never leaves a half-saved draft.
        clarification = (parsed.get("clarification_message")
                         if final_status == "needs-clarification" else None)
        storage.save_processed_order(
            record.request_id, record.order_ref, final_status, lines_with_pricing,
            findings, clarification, model=llm_result["model"], mode=mode,
            raw_response=llm_result.get("raw_response"), db_path=db_path,
        )

        return {
            "status": final_status,
            "request_id": record.request_id,
            "order_ref": record.order_ref,
            "lines_count": len(lines_with_pricing),
        }

    except Exception as exc:
        # Any failure is recorded but does not stop the batch. Model problems already
        # carry a category like "[refusal]"; anything else is labelled internal.
        if isinstance(exc, LLMProcessingError):
            error_msg = str(exc)
        else:
            error_msg = f"[internal-error] {type(exc).__name__}: {exc}"
        try:
            storage.upsert_order(record.order_ref, "failed", db_path=db_path)
            storage.insert_processing_attempt(
                record.request_id, record.order_ref, "failed",
                model, mode, error_message=error_msg, db_path=db_path
            )
        except Exception:
            pass  # DB might not be initialised; best effort
        return {
            "status": "failed",
            "request_id": record.request_id,
            "order_ref": record.order_ref,
            "error": error_msg,
        }


def process_all_requests(data_dir: str = "data", mode: str = "live",
                          db_path: str = storage.DB_PATH) -> list:
    """
    Load all requests from the manifest and process each one.
    Failed items (including unreadable files) are included in the returned list;
    they don't stop the batch.
    """
    records, file_errors = load_requests_tolerant(Path(data_dir))
    results = [record_file_error(e, mode, db_path) for e in file_errors]
    results += [process_one_request(r, mode, db_path) for r in records]
    return results


def record_file_error(error: dict, mode: str, db_path: str = storage.DB_PATH) -> dict:
    """Save a visible failed attempt for a request file that could not be read."""
    storage.insert_request(error["request_id"], error["order_ref"], "",
                           error["source_file"], db_path)
    if storage.find_existing_order_for_ref(error["order_ref"], db_path) is None:
        storage.upsert_order(error["order_ref"], "failed", db_path=db_path)
    storage.insert_processing_attempt(error["request_id"], error["order_ref"], "failed",
                                      DEFAULT_MODEL, mode, error_message=error["error"],
                                      db_path=db_path)
    return {"status": "failed", "request_id": error["request_id"],
            "order_ref": error["order_ref"], "error": error["error"]}


def retry_failed_requests(data_dir: str = "data", mode: str = "live",
                           db_path: str = storage.DB_PATH) -> list:
    """
    Retry only requests whose order is currently in 'failed' status.
    Retrying never creates a second order: orders are keyed by order_ref.
    """
    records, file_errors = load_requests_tolerant(Path(data_dir))
    results = []
    for error in file_errors:
        order = storage.find_existing_order_for_ref(error["order_ref"], db_path)
        if order and order["status"] == "failed":
            results.append(record_file_error(error, mode, db_path))
    for record in records:
        order = storage.find_existing_order_for_ref(record.order_ref, db_path)
        if order and order["status"] == "failed":
            storage.update_request_text(record.request_id, record.text, db_path)
            results.append(process_one_request(record, mode, db_path))
    return results


def apply_correction_and_revalidate(order_ref: str, line_id: int, field_name: str,
                                     new_value, original_text: str,
                                     db_path: str = storage.DB_PATH) -> list:
    """
    Apply a reviewer correction to one line field, recompute pricing, revalidate.

    Steps:
    1. Read current value.
    2. Insert correction record.
    3. Update the field on the line.
    4. If sku or quantity changed, recompute all pricing for the line.
    5. Run all validation checks.
    6. If all lines are now resolved and checks pass, update order to 'draft'.
    7. Return the new list of validation findings.
    """
    # 1. Read current line.
    lines = storage.get_order_lines(order_ref, db_path)
    target = next((l for l in lines if l["id"] == line_id), None)
    if target is None:
        raise ValueError(f"Line {line_id} not found for order {order_ref!r}")

    previous_value = target.get(field_name)
    if field_name == "sku":
        new_value = str(new_value).strip().upper()  # SKUs are stored in catalog form

    # 2. Record the correction.
    storage.insert_correction(
        order_ref, line_id, field_name,
        str(previous_value) if previous_value is not None else None,
        str(new_value),
        db_path=db_path,
    )

    # 3. Update the field. A reviewer-chosen SKU gets its own catalog lookup as
    #    evidence (the model's earlier lookup was for a different SKU or none).
    storage.update_order_line_field(line_id, field_name, new_value, db_path)
    if field_name == "sku":
        result = catalog_module.search_catalog(
            new_value, catalog_module.load_catalog(CATALOG_PATH))
        confirmed = bool(result.get("found")) and result.get("sku") == new_value
        storage.update_order_line_evidence(line_id, {
            "confirmed": confirmed, "source": "reviewer correction",
            "searches": [{"query": new_value, "result": result}],
        }, db_path)

    # 4. Recompute pricing for the line. If the SKU is unknown or the quantity is
    #    missing, clear the old prices so a stale total is never shown.
    lines = storage.get_order_lines(order_ref, db_path)  # reload
    target = next(l for l in lines if l["id"] == line_id)
    current_sku = target.get("sku")
    current_qty = target.get("quantity")

    catalog = catalog_module.load_catalog(CATALOG_PATH)
    product = catalog_module.get_product_by_sku(current_sku, catalog) if current_sku else None
    if product and current_qty is not None:
        pricing = compute_line_total(int(current_qty), product.unit_cents)
        storage.update_order_line_pricing(
            line_id,
            unit_cents=product.unit_cents,
            subtotal_cents=pricing["subtotal_cents"],
            discount_cents=pricing["discount_cents"],
            line_total_cents=pricing["line_total_cents"],
            is_unresolved=False,
            db_path=db_path,
        )
    else:
        storage.update_order_line_pricing(
            line_id, unit_cents=None, subtotal_cents=None, discount_cents=None,
            line_total_cents=None, is_unresolved=True, db_path=db_path,
        )

    # 5. Run validation on the updated lines.
    lines = storage.get_order_lines(order_ref, db_path)
    findings = validation.run_all_checks(lines, original_text, CATALOG_PATH)

    storage.delete_validation_findings(order_ref, db_path)
    storage.insert_validation_findings(order_ref, findings, db_path)

    # 6. Validation status: draft only if every line is resolved and every check passes.
    all_resolved = all(not l.get("is_unresolved") for l in lines)
    all_checks_pass = all(f["passed"] for f in findings)
    if all_resolved and all_checks_pass:
        storage.update_order_status(order_ref, "draft", db_path)
    else:
        storage.update_order_status(order_ref, "needs-clarification", db_path)

    # 7. Review status is separate: any change to a reviewed order needs a new review.
    order = storage.get_order(order_ref, db_path)
    if order["review_status"] == "reviewed":
        storage.update_order_review_status(order_ref, "pending", db_path)
        storage.insert_correction(order_ref, None, "review_status", "reviewed", "pending",
                                  actor="system", db_path=db_path)

    return findings


def exception_reason_counts(db_path: str = storage.DB_PATH) -> dict:
    """
    Count why orders are not ready, for the 'Common exception reasons' panel.
    Returns lists of (reason, count), most common first:
      failed_checks      - validation checks that failed, per order
      unresolved_reasons - unresolved lines grouped into a few plain categories
      processing_errors  - technical failure categories of the latest attempt per request
    """
    from collections import Counter
    failed_checks, unresolved, errors = Counter(), Counter(), Counter()
    for order in storage.list_orders(db_path=db_path):
        ref = order["order_ref"]
        for finding in storage.get_validation_findings(ref, db_path):
            if not finding["passed"]:
                failed_checks[finding["check_name"]] += 1
        for line in storage.get_order_lines(ref, db_path):
            if not line["is_unresolved"]:
                continue
            searches = (line.get("lookup_evidence") or {}).get("searches", []) \
                if isinstance(line.get("lookup_evidence"), dict) else []
            if not line["sku"]:
                if any(s.get("result", {}).get("candidates") for s in searches):
                    unresolved["ambiguous product (several catalog candidates)"] += 1
                else:
                    unresolved["unknown product (no catalog match)"] += 1
            if line["quantity"] is None:
                unresolved["missing or non-item quantity"] += 1
    for request in storage.list_requests(db_path):
        attempts = storage.get_processing_attempts(request["id"], db_path)
        if attempts and attempts[-1]["status"] == "failed":
            message = attempts[-1]["error_message"] or ""
            category = message[1:message.index("]")] if message.startswith("[") and "]" in message \
                else "other"
            errors[category] += 1
    return {"failed_checks": failed_checks.most_common(),
            "unresolved_reasons": unresolved.most_common(),
            "processing_errors": errors.most_common()}


def mark_reviewed(order_ref: str, actor: str = "human",
                  db_path: str = storage.DB_PATH) -> None:
    """
    Explicit reviewer action. Only a valid draft (all lines resolved, all checks
    passed) can be marked reviewed. Raises ValueError otherwise.
    """
    order = storage.get_order(order_ref, db_path)
    if order is None:
        raise ValueError(f"Order {order_ref!r} not found.")
    findings = storage.get_validation_findings(order_ref, db_path)
    lines = storage.get_order_lines(order_ref, db_path)
    if (order["status"] != "draft" or not lines
            or any(l["is_unresolved"] for l in lines)
            or not all(f["passed"] for f in findings)):
        raise ValueError(
            f"Order {order_ref} cannot be marked reviewed: it is not a valid draft "
            f"(status: {order['status']})."
        )
    if order["review_status"] == "reviewed":
        return
    storage.update_order_review_status(order_ref, "reviewed", db_path)
    storage.insert_correction(order_ref, None, "review_status", order["review_status"],
                              "reviewed", actor=actor, db_path=db_path)
