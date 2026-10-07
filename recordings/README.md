# Recordings of real model calls

Every `.json` file in this folder tree was written by `src/llm.py` during a **real**
OpenAI API call made from this project on 2026-10-07 (account of the candidate,
model requested `gpt-4.1-mini`). No file was edited by hand. Test doubles used by
the unit tests never write here (they use a temporary directory and are labelled
`TEST-ONLY` / `fake-model-for-tests`).

Recordings contain the prompt, request text, tool calls, catalog tool results, the
final answer and response metadata. They contain no API key or HTTP headers
(checked with a search for `sk-`, `authorization` and `bearer`; see
`docs/verification.md`).

| Folder | Format | Used by replay? | What it is |
|---|---|---|---|
| `recordings/*.json` | 2 | **Yes** | Current configuration. One file per request that calls the model (R1–R3, R5–R11; R4 is a duplicate and never calls the model). |
| `recordings/*.json` timing | | | Calls started 18:23:44–18:24:08 UTC. |
| `recordings/superseded-search-v1/` | 2 | No | First run with the strict schema (calls started 18:21:31–18:22:18 UTC). Superseded because the catalog search code changed after this run (R7 regression, see below). Kept as evidence. |
| `recordings/legacy-v1/` | 1 | No | First live run (saved 17:43:14–17:43:51 UTC) with the original prompt and no output schema. Kept as evidence. `run-metadata.json` holds that run's processing-attempt rows (exported from its local database). |

## Format 2 (current)

File name: `<request_id>_<first 12 hex chars of fingerprint>.json`.

Key fields:

- `fingerprint_components`: everything that can change the answer -
  `request_id`, `order_ref`, `user_message_sha256` (metadata + customer text),
  `system_prompt_sha256`, `model` (requested), `catalog_sha256`,
  `tool_definition_sha256`, `tool_implementation_sha256` (source of
  `normalize_words` + `search_catalog`), `output_schema_sha256` (response_format
  schema + Pydantic schema), loop `settings`, `recording_format`.
- `fingerprint`: SHA-256 of `fingerprint_components`.
- `requested_model` / `response_model`: the model asked for and the exact model id
  the API reported (`gpt-4.1-mini-2025-04-14`).
- `rounds[]`: per API request - `request_started_at` (UTC), `response_id`,
  `response_model`, `finish_reason`, `refusal`, token `usage`.
- `tool_calls[]`: validated arguments and the catalog result returned to the model.
- `final_response`: the model's final text; `error`: set if the call failed
  (replay then reproduces the same failure).

Replay recomputes the fingerprint from the current code and data. If no file
matches exactly, it fails with `[replay-missing]` or `[replay-mismatch]` and lists
which components differ (for example `differs in tool_implementation_sha256`).

## Format 1 (`legacy-v1/`)

Fields: `recording_version: 1`, `request_id`, `recording_key`, `model`,
`timestamp`, `messages`, `tool_calls`, `tool_results`, `final_response`.
`recording_key = sha256(request_id + sorted JSON of [system, user] messages + model)`.

What `scripts/verify_legacy_recordings.py` establishes for all 10 files:

| Item | Status |
|---|---|
| Request text, system prompt, requested model | **Verified**: each `recording_key` is reproduced from the request file, `ai-workflow/prompt-snapshots/system-prompt-v1.txt` and `gpt-4.1-mini`. |
| Every resolved SKU has a successful lookup for that SKU in the same file | **Verified** (0 problems). R3 made **no** catalog call at all. |
| Catalog | **Consistent, not proven**: prices in the tool results equal the current `data/catalog.json`; the catalog itself was not recorded. |
| Tool definition / search code | **Not verifiable** from the file. Git history starts after this run. |
| Output schema | None was sent (format was described in the prompt only). |
| Model id reported by the API, response ids, finish reasons | **Not in the recording files.** The run's database stored them for the final API response of each call: all 10 report `gpt-4.1-mini-2025-04-14` and `finish_reason=stop` (`legacy-v1/run-metadata.json`). Earlier tool-call rounds were not stored. |

## Why `superseded-search-v1/` exists

With the stricter prompt, the model searched R7 ("10 USB-C 2-meter cables") as
`"USB-C 2 meter cable"`. The old search only matched whole description phrases, so
it returned two candidates and R7 became needs-clarification, contrary to the answer
key. The search was changed to match when all words of one description appear in
the query (units/plurals normalized). Because the search code is part of the
fingerprint, those 10 recordings no longer match and all requests were recorded again.
