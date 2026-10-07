# Verification report

All results below were observed on 2026-10-07 on Windows 11, Python 3.12.3, with the
pinned packages in `requirements.txt`. Three kinds of evidence are kept apart:

| Kind | What it proves | Uses the real API? |
|---|---|---|
| **A. Live checks** | The real model + tool loop produces the expected drafts | Yes (real calls, recorded) |
| **B. Replay checks** | The same results are reproduced from the real recordings, without a key | No (real recordings) |
| **C. Offline pytest** | Rules, failures, storage, matching, using labelled **test doubles** | No |
| **D. Streamlit AppTest** | The real `app.py` screen: correction, review, restart | No (replays real recordings) |

The answer key is `data/expected-results.json`. `scripts/check_expected.py` compares
a database with it and recomputes every total from the seed catalog with its own
arithmetic (it does not call `src/pricing.py`).

## Required demonstrations: expected vs observed

| # | Expected (answer key) | Observed | Evidence |
|---|---|---|---|
| 1 | **R1** → draft CAB-1 × 2, total 4000 cents | Draft CAB-1 × 2; subtotal 4000, discount 0, total 4000; tool call `search_catalog("CAB-1")` → found CAB-1 2000; all 5 checks passed | A, B, D (`recordings/R1_977504bb5dce.json`) |
| 2 | **R2** → needs clarification, unknown product | Needs clarification; SKU null, not priced; `search_catalog("Moon adapter")` → no match, no candidates; clarification draft: *"Could you please provide more details or a different description for the 'Moon adapter' you want to order?"* | A, B |
| 3 | **R3** → needs clarification, product and item quantity ambiguous | Needs clarification; SKU null **and** quantity null; `search_catalog("cable")` → not found, candidates CAB-1 and CAB-2; clarification asks which cable and how many individual cables because of "two boxes"; no price shown; UI says the searches did not identify one product | A, B, D |
| 4 | **R4** → duplicate of O1, 0 new drafts | `duplicate`, linked `R4 → R1`; no model call; still 10 orders and 1 line on O1; O1 stays `draft`. Processing all 11 requests a second time: 0 new orders, 0 model calls (all skipped) | A, B, D |
| 5 | **R10 correction** → reviewer sets quantity 3 → 3 × 2000 = 6000 cents, survives restart | Before: needs clarification, CAB-1 confirmed by lookup, quantity null, Mark reviewed disabled. After Save: draft, $60.00, checks rerun, no model call. Mark reviewed → reviewed. New app session (restart): still draft/reviewed, $60.00, history `quantity: (none) → 3 (by human)` and `review_status: pending → reviewed (by human)`. Reprocessing all requests afterwards leaves quantity 3 / 6000 cents | D (`tests/test_app_ui.py::test_r10_correction_review_and_restart`), C |

Other fixtures (live and replay, all **OK**): R5 CAB-1 × 9 = 18000; R6 CAB-1 × 10 =
18000 (2000 discount); R7 CAB-2 × 10 = 27000 (3000 discount, matched by description
words from "USB-C 2-meter cables"); R8 HUB-1 × 2 = 10000; R9 CAB-1 × 12 = 21600;
R11 CAB-1 × 1 + HUB-1 × 1 = 7000 (two lookups).

## A. Live checks (real API calls)

Command: `python -m src.cli --mode live --only R1`, then the other ten requests.

- Requested model `gpt-4.1-mini`; every response reported `gpt-4.1-mini-2025-04-14`.
  OpenAI Python SDK 3.26.0. Calls started 18:23:44–18:24:08 UTC.
- `python scripts/check_expected.py orders.db` → **11/11 OK, 10 distinct orders, 0 mismatches.**
- Every call ended with `finish_reason=stop`, no refusals.
- Every resolved SKU is backed by a successful `search_catalog` result for that SKU
  (`lookup_evidence_present` passed on all orders).
- Recordings were searched for `sk-` followed by key characters, `authorization` and
  `bearer`: no matches.

Real failure seen during development (not simulated): the very first live R1 call
returned HTTP 429 `insufficient_quota`. It was stored as a failed attempt with the
API's message and O1 = `failed`; after credits were added, the retry produced the
draft with no extra order (`recordings/legacy-v1/run-metadata.json`).

Earlier real runs are kept, not used for replay: `recordings/legacy-v1/` (first run,
format 1) and `recordings/superseded-search-v1/` (strict-schema run in which R7
became needs-clarification; that led to the catalog-search fix). See
`recordings/README.md`.

## B. Replay without credentials

Command (PowerShell): `$env:OPENAI_API_KEY = ""; python -m src.cli --mode replay --db replay-check.db`

- 11 requests, 0 failures; `scripts/check_expected.py` → **11/11 OK, 10 distinct orders.**
- Processing everything a second time: all skipped / duplicate; still 10 orders.
- A recording made under a different configuration is refused clearly, e.g. with the
  superseded R1 recording only:
  `[replay-mismatch] No recording for request R1 matches the current configuration (expected R1_977504bb5dce.json). Found: R1_b9512d1c3424.json: differs in tool_implementation_sha256`
- A missing recording gives `[replay-missing] No recording for request R8 ...`
  (observed earlier with the format-1 code; the current code is covered by
  `tests/test_llm.py::TestReplayMatching::test_missing_recording`).

## C. Offline tests (pytest, test doubles)

`python -m pytest -q` → **164 passed** (latest run, see the end of this file).

| File | Tests | Covers |
|---|---|---|
| `tests/test_pricing.py` | 22 | 4000 / 18000 / 18000 / 27000 / 21600, discount boundary 9/10, half-up rounding (test-only unit prices), invalid quantities |
| `tests/test_catalog.py` | 22 | SKU match, description match, R7 wording, ambiguity, candidates for "usual cable", bounded candidates, runtime catalog equals the seed catalog |
| `tests/test_validation.py` | 27 | the five checks; evidence must match the exact SKU |
| `tests/test_llm.py` | 25 | fake OpenAI client: refusal, content filter, truncated (`length`), empty output, malformed JSON, schema violations, 7 kinds of invalid tool arguments, unknown tool, round limit, API error, no key; recording format; replay missing / mismatch by model, prompt, catalog, text; legacy file reported |
| `tests/test_processing.py` | 40 | R1–R4/R6/R10 flows with test doubles, invalid model quantities (0, -1, 1.5, true, "2"), SKU without lookup not confirmed, failure isolation, malformed/missing files, duplicates, changed requests, review status |
| `tests/test_storage.py` | 21 | SQLite CRUD and duplicate lookup |
| `tests/test_schemas.py` | 2 | API JSON schema and Pydantic model list the same fields |
| `tests/test_app_ui.py` | 5 | see D |

Test doubles are labelled in `tests/conftest.py` ("TEST-ONLY: not a real API call")
and `tests/test_llm.py` (`FakeOpenAI`, model `fake-model-for-tests`). They write
only to temporary directories.

Real refusals, truncated responses, timeouts and invalid tool arguments did **not**
occur in the live runs, so those paths are verified only with test doubles.

## D. Streamlit AppTest (real `app.py`, headless)

`tests/test_app_ui.py` replays the real recordings into a temporary database with
`OPENAI_API_KEY` empty, then drives the real widgets:

- Summary counts: ready for review 7, needs clarification 3, processing failed 0,
  reviewed 0, distinct orders 10, duplicate requests 1, changed requests 0.
- O1: $40.00, caption "REPLAYED from saved real-call recording", R4 link shown.
- O3: no price, no "confirmed match", "did not identify one product".
- O9 (R10): correction flow and restart as in demonstration 5.
- Invalid quantity `abc`: error shown, nothing saved.

Separately, the final version started with
`python -m streamlit run app.py --server.headless true --server.port 8599` and
answered `/_stcore/health` with `200 ok`.

**Not verified:** a manual click-through in a real browser.

## E. Legacy recordings

`python scripts/verify_legacy_recordings.py` → 10 checked, 0 problems: every stored
key is reproduced from the request text, the saved v1 prompt and `gpt-4.1-mini`;
every resolved SKU has a matching successful lookup; tool prices equal the current
catalog. R3 in that run made no catalog call (the reason for the prompt change).

## Latest run log

```
python -m pytest -q                         -> 164 passed
python scripts/check_expected.py orders.db  -> 11 OK, 0 mismatches (live database)
replay with empty OPENAI_API_KEY + check    -> 11 OK, 0 mismatches
python scripts/verify_legacy_recordings.py  -> 10 checked, 0 problems
```
