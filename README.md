# AI Order Intake & Exception Handling Platform

A small local application (Junior AI Engineer take-home, Alternative A) that turns
fictional customer order messages into draft orders, makes uncertainty visible, and
lets a person correct and review the result. Python 3.12, Streamlit, SQLite,
Pydantic, OpenAI Python SDK, pytest.

- A model (default `gpt-4.1-mini`) reads each message and **must** look products up
  with a local `search_catalog` tool. It proposes lines, source quotes and a
  clarification question.
- Ordinary Python validates the answer, checks it against the catalog and the
  original text, and computes all prices in integer cents.
- Every real model call is recorded, so the whole flow can be **replayed without an
  API key**.
- Verification results: [`docs/verification.md`](docs/verification.md).

---

## 1. Setup

Requires Python 3.12. Tested on Windows 11 with Python 3.12.3.

**Windows PowerShell**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env      # only needed for live mode; put your key in .env
```

**macOS / Linux** (same steps; not run on these systems during this exercise)
```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

`.env` is git-ignored. Never paste the key anywhere else.

## 2. Run

| What | Command (inside the activated venv) |
|---|---|
| App | `python -m streamlit run app.py` → http://localhost:8501 |
| Tests (no key needed) | `python -m pytest -q` |
| Process from the command line | `python -m src.cli --mode replay` or `--mode live`; add `--only R1 R2` and/or `--db other.db` |
| Compare a database with the answer key | `python scripts/check_expected.py orders.db` |
| Check the legacy recordings | `python scripts/verify_legacy_recordings.py` |

### Replay (no API key)
In the app: keep **replay** selected in the sidebar → **Process All** → pick an order
in the queue. The detail view shows "REPLAYED from saved real-call recording" and
the recording file name.

Replay on the command line with no key at all (PowerShell):
```powershell
$env:OPENAI_API_KEY = ""          # make sure no key is used, even if .env has one
python -m src.cli --mode replay --db replay-check.db
python scripts/check_expected.py replay-check.db
Remove-Item Env:OPENAI_API_KEY
```

### Live
Put `OPENAI_API_KEY` in `.env`, select **live** in the sidebar (or `--mode live`).
Each real call is saved in `recordings/`. Orders that already exist are not sent to
the model again; use a fresh `DB_PATH` / `--db` to record again.

Starting from an empty database: **Process All** in either mode loads the request
files itself. **Load Requests** only stores the original texts. **Retry Failed**
reprocesses orders whose last attempt failed.

## 3. Architecture

```
app.py                 Streamlit screen (rendering only; calls src/)
src/
  loader.py            Reads data/manifest.json + data/requests/*.txt; one bad file does not stop the batch
  catalog.py           Loads the catalog; search_catalog() = the model's tool (SKU, description, candidates)
  schemas.py           Pydantic models: tool arguments and the model's final answer (+ strict JSON schema)
  llm.py               Prompt, tool loop, failure categories, fingerprint, recording + replay
  validation.py        Five fact checks on a draft (SKU exists, quote in text, quantity, lookup evidence, boxes)
  pricing.py           Integer-cent pricing and the 10% line discount
  processing.py        Pipeline: dedup -> model -> validate -> price -> save; corrections; review
  storage.py           All SQLite access (one module, no SQL elsewhere)
  cli.py               Command-line runner
scripts/               check_expected.py (answer key), verify_legacy_recordings.py
data/                  Fixtures, manifest, answer key; data/seed/ = untouched starter files
recordings/            Real-call recordings (see recordings/README.md)
tests/                 pytest (test doubles labelled; AppTest for the UI)
ai-workflow/           AI tools/models/prompts used during development
docs/verification.md   Expected vs observed results
```

**One request, step by step** (`processing.process_one_request`, never raises):

1. Save the original request (`requests` table, with its source file name).
2. Same order_ref **and** same text as an earlier request → mark as duplicate
   (`requests.duplicate_of`) and stop. No model call, no new order.
3. Same order_ref, **different** text → flag as changed (`requests.conflicts_with`)
   and stop (see "Rule ambiguities and implementation choices").
4. Order already processed and not failed → skip (protects human corrections).
5. Model call (`llm.call_llm`): up to 5 model turns, `search_catalog` tool,
   strict JSON-schema answer, 60 s timeout, 2 SDK retries.
6. Pydantic validates the answer. Refusal, truncation, empty answer, bad JSON,
   schema violation, invalid tool arguments, API errors → **processing failed**
   with a category such as `[incomplete]`. Other requests continue.
7. Catalog evidence is taken **only from tool calls that really ran**. A SKU without
   a lookup that found that exact SKU is marked unresolved and is not priced.
8. Python prices resolved lines; the five checks run; status is `draft` only if all
   lines are resolved and all checks pass, otherwise `needs-clarification`.
9. Order, lines, findings, clarification draft and attempt are saved in one
   SQLite transaction.

**Validation status vs review status.** `orders.status` (draft /
needs-clarification / failed) comes from code. `orders.review_status` (pending /
reviewed) changes only when a person clicks **Mark reviewed**, which is enabled
only for a valid draft. **Save** on a line reruns pricing and checks without a model
call. If the order was reviewed, it goes back to `pending`. Every change is stored in
`corrections` with old value, new value, actor and timestamp.

**Summary counts.** The dashboard has two labelled rows.
- **Requests:** *Requests processed* (request files with at least one processing
  attempt, duplicates included), *Duplicate requests* and *Changed requests*.
- **Orders** (one per order_ref): *Distinct orders*, *Orders ready for review*
  (valid drafts not yet reviewed), *Orders needing clarification*, *Orders failed
  processing* and *Orders reviewed*.

With the sample data that gives 11 requests, 1 duplicate and 10 orders. The queue
lists orders.

## 4. Data and mapping

| Source | Repository location |
|---|---|
| Starter `tasks/orders/domain.md`, `seed.json`, `expected-seed-results.json`, `request.template.json` | `data/seed/` (byte-identical copies of the files in `client-ai-starter-pack.zip`) |
| Seed catalog (`seed.json` → `catalog`) | `data/catalog.json` (field `name` renamed `description`; a test checks it equals the seed) |
| Seed requests R1–R4 | `data/requests/R1.txt`–`R4.txt`, text unchanged |
| Added requests R5–R11 | `data/requests/R5.txt`–`R11.txt` |
| id ↔ order_ref ↔ file | `data/manifest.json` |
| Answer key (all 11) | `data/expected-results.json` (separate from app output) |

| id | order_ref | Text | Expected |
|---|---|---|---|
| R1 | O1 | Please send 2 individual CAB-1 cables. | draft, 4000 |
| R2 | O2 | Please send one Moon adapter. | needs clarification (unknown product) |
| R3 | O3 | Send two boxes of the usual cable. | needs clarification (product + box quantity) |
| R4 | O1 | (same as R1) | duplicate of O1, 0 new drafts |
| R5 | O4 | Please send 9 CAB-1 cables. | draft, 18000 (no discount) |
| R6 | O5 | Please send 10 CAB-1 cables. | draft, 18000 (20000 − 2000) |
| R7 | O6 | We need 10 USB-C 2-meter cables. | draft CAB-2, 27000 (description match) |
| R8 | O7 | Please send 2 HUB-1 units. | draft, 10000 |
| R9 | O8 | Please send 12 CAB-1 cables. | draft, 21600 |
| R10 | O9 | Please send some CAB-1 cables. | needs clarification; reviewer sets 3 → 6000 |
| R11 | O10 | Please send 1 CAB-1 cable and 1 HUB-1 unit. | draft, 2000 + 5000 = 7000 |

**How R5–R11 were made.** They were written by hand (no random generation, so no
seed) in the first development session with the coding assistant, to cover the
brief's list: 9/10/12 items, 10 × CAB-2, 2 × HUB-1, a description match, a missing
quantity and a multi-line order. The exact prompt of that session is not available
(see `ai-workflow/README.md`). No box sizes, products, charges or policies were
added. There are no intentionally invalid fixture files in `data/`. Malformed-file
cases exist only as test-only files created by `tests/test_processing.py`.

**Checking the answer key.** `scripts/check_expected.py` recomputes every expected
total from the seed prices with its own arithmetic, independent of `src/pricing.py`.
The five reference scenarios (R1, R2, R3, R4, R10) still need to be inspected by the
candidate; see Pending.

## 5. Model configuration

| Setting | Value |
|---|---|
| Application model | `gpt-4.1-mini` (`OPENAI_MODEL`); the API reported `gpt-4.1-mini-2025-04-14` |
| API | OpenAI Chat Completions, SDK `openai==3.26.0` |
| Tool | `search_catalog(query)`, `strict: true`, `additionalProperties: false` |
| Output | `response_format` = strict JSON schema `order_draft` (`src/schemas.py`), re-validated with Pydantic |
| Other | `tool_choice=auto`, `parallel_tool_calls=false`, temperature = API default, max 5 model turns, timeout 60 s, `max_retries=2` |
| Prompt | `src/llm.py` `SYSTEM_PROMPT`; snapshots in `ai-workflow/prompt-snapshots/` |
| Key | `OPENAI_API_KEY` from `.env` (never logged or recorded) |
| Development assistant | Claude Code (separate from the app model; see `ai-workflow/README.md`) |

The existing Chat Completions tool loop was kept (it was already working) rather than
migrated to the Responses API. Strict function tools and strict `response_format`
are both supported there.

## 6. Recording and replay

A recording is matched by a **fingerprint** of the request id, order_ref, request
message, system prompt, requested model, catalog, tool definition, search-code
source, output schema and loop settings. If any of them change, replay refuses with
`[replay-mismatch] ... differs in <component>` instead of reusing a stale answer. A
missing file gives `[replay-missing]`. A recorded failure replays as the same
failure. Details, formats, and the older recordings (`legacy-v1/`,
`superseded-search-v1/`): [`recordings/README.md`](recordings/README.md).

## 7. Rule ambiguities and implementation choices

The supplied rules (`data/seed/domain.md`) leave some cases open. These are the
choices made, so a reviewer can disagree with a specific decision rather than
discover it in the code.

1. **Product matching.** The rules say "SKU or an unambiguous catalog description"
   but do not define *unambiguous*. `search_catalog` tries these steps in order:
   1. An exact SKU (case-insensitive).
   2. A description that contains the query (`"USB hub"`).
   3. All words of exactly **one** description appear in the query, after
      normalizing plurals and units (`"USB-C 2-meter cables"` → CAB-2).

   Anything matching more than one product (`"cable"`, `"USB-C cables"`) or nothing
   (`"Moon adapter"`, `"USB-C cable 3 m"`) is **not found**. Products that share a
   word are returned as at most 5 *candidates* for a person to choose from, and are
   never picked automatically. The model may only propose a SKU that a lookup
   actually returned; otherwise the line stays unresolved.
2. **Number words.** The rules say quantities are positive whole numbers of
   individual items, but customers write "one" or "two". The **model** converts
   number words to integers ("one Moon adapter" → 1), and the code then only accepts
   positive integers. Booleans, 0, negatives, decimals and numeric strings make the
   answer invalid. The code checks that the cited quote ("one Moon adapter") appears
   in the message. It does **not** independently re-parse the number word, so a
   reviewer should glance at quantity and quote side by side, as the review screen
   shows them. Vague amounts ("some", "the usual") stay null.
3. **Boxes are never items.** The rules forbid inferring box contents, so "two
   boxes" leaves the quantity null. As a code-level guard, a check fails if any
   quantity is resolved for a message that mentions boxes.
4. **Changed requests sharing an order reference.** The rules say requests with the
   same order reference describe the same order, but not what to do when the text
   differs. The supplied rules do not define merging, so this is our chosen
   behaviour:
   - *Identical text* (R4 repeats R1): the request is saved and linked to the
     original (`requests.duplicate_of = R1`). There is no model call and no new
     order, and the existing order's status, lines and corrections are not touched.
   - *Different text*: the new request is saved and flagged
     (`requests.conflicts_with = <original request>`). The model is not called and
     the existing lines are **not** changed. The order goes back to review status
     `pending`, and the UI shows a warning with the new text so a person decides.
5. **Half-up rounding: a testing limitation.** The rule says to round the 10%
   discount to the nearest cent, with halves rounded up. The code implements this
   as `(subtotal + 5) // 10`. However, every supplied unit price (2000, 3000, 5000
   cents) is a multiple of 10, so a 10% discount is always a whole number of cents.
   The supplied catalog and fixtures therefore never exercise fractional-cent
   rounding. The half-up and round-down branches are covered only by unit tests
   with **test-only** unit prices (1005 and 1001 cents in `tests/test_pricing.py`),
   which are not catalog prices and do not change the supplied catalog.
6. **Failed checks** (for example a quote that is not in the text) make an order
   needs-clarification, not ready for review, and the failed check is shown.
7. **Reviewer SKU corrections.** A reviewer-entered SKU gets its own catalog lookup
   as evidence (`source: reviewer correction`).

## 8. Evidence-based intake improvement

From the real results (Common exception reasons panel), 3 of 10 orders need
clarification, and all three are caused by how the message was written, not by the
model: one unknown product name (R2), one vague product plus boxes (R3), and two
lines with a missing or non-item quantity (R3, R10). **Improvement:** give customers
an order template (or form) that asks for *SKU or exact catalog name* and *number of
individual items*. All three clarifications in this data would be avoided.

A second, already implemented, improvement also came from a real result: in the
second live run the model searched R7 as "USB-C 2 meter cable". The old phrase-only
search returned two candidates, so a clear order needed clarification. The search
now normalizes units and plurals (see `recordings/README.md`).

## 9. Walkthrough (presentation script, ~5 minutes)

1. **Start**: `python -m streamlit run app.py`, mode **replay**, **Process All**.
   The counts show 11 requests processed, 1 duplicate request, 10 distinct orders,
   7 orders ready for review, 3 needing clarification and 0 failed. Explain that
   replay uses saved real calls, not fakes.
2. **Normal (O1 / R1)**: original text next to the draft. CAB-1 × 2 = **$40.00**.
   Evidence shows `search_catalog("CAB-1")` → found. All 5 checks are green.
   Python computed the price, not the model.
3. **Unclear product (O2 / R2)**: "Moon adapter" → search found nothing, no price,
   clarification draft asks for details. **Unclear quantity (O3 / R3)**: the model
   searched "cable" and got CAB-1 and CAB-2 as candidates. Product and quantity both
   stay unresolved, because "two boxes" is never turned into 2 items.
4. **Duplicate (O1)**: "Request R4 repeats request R1 and is linked to this order".
   Still 10 orders. Click Process All again: nothing new is created.
5. **Reviewer correction (O9 / R10)**: "some CAB-1 cables". Mark reviewed is
   disabled. Type 3 → **Save**: draft, **$60.00**, checks rerun with no model call.
   **Mark reviewed** (Orders reviewed becomes 1). Stop and restart the app: the
   correction, the review and the revision history are still there.
6. **Improvement**: open *Common exception reasons* and explain section 8.

## 10. Time spent

Not measured with a timer. The file and recording timestamps show the AI-assisted
implementation, live runs, verification and documentation on 2026-10-07, from about
20:30 to about 21:50 local time. This does **not** include the candidate's own
reading, review and presentation preparation. **Pending:** the candidate should
replace this section with their own observed total. An earlier version of this
README claimed "~5.5 hours (estimated)"; that figure was not based on a measurement
and has been removed.

## 11. Limitations

- Real refusals, truncated answers, timeouts and invalid tool arguments did not
  happen in the live runs. Those paths are tested only with labelled test doubles.
- A manual click-through in a browser was not done. The UI was verified with
  Streamlit AppTest, which runs the real `app.py` headlessly.
- macOS/Linux setup commands were not run.
- The queue lists orders (order_refs). Duplicate and changed requests are shown
  inside their order, not as separate queue rows.
- Only one order version is kept. Changed requests are flagged, not merged.
- Changing the model, prompt, catalog, schema or search code invalidates the
  recordings, by design. Record again in live mode.
- `Mark reviewed` uses the actor name "human". There is no authentication (out of
  scope).
- Model output is non-deterministic. A new live run can phrase clarifications
  differently, so the answer key checks outcomes, not exact wording.

## 12. Pending (needs the candidate)

1. Inspect the five reference expectations (R1, R2, R3, R4, R10) in
   `data/expected-results.json` against `data/seed/domain.md`.
2. Replace the time-spent section with your own observed time.
3. Confirm the development model of the first session (see `ai-workflow/README.md`).
4. Optional: one manual click-through of the walkthrough in a browser.
