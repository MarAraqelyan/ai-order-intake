# Development prompt (assignment brief given to the coding assistant)

Saved verbatim from the Claude Code session on 2026-10-07 in which this file was written.

**Provenance and caveats**
- In that session the brief arrived inside a short request. The text before it was
  `check what's currently implemented for this applitc` and the text after it was
  `ation and what is left` (the request was "check what's currently implemented for this
  application and what is left", with the brief pasted into the middle of the word
  "application"). The brief itself is reproduced below unchanged.
- Code already existed when that session started (files dated 2026-10-07 20:32–20:50
  local time). The prompts used in that earlier session are **not available** in this
  session and are not reproduced here; see `README.md` → "Tools and models".
- The follow-up instructions given later in the same session are in
  `session-instructions.md`.

---

You are helping me implement a Junior AI Engineer take-home assignment: Alternative A, “AI Order Intake & Exception Handling Platform.” Build a complete, small local application that converts fictional customer messages into draft orders, makes uncertainty visible, and allows a person to review and correct the results.
The assignment has an eight-hour total time budget, including data preparation, verification, and documentation. Prioritize a reliable required flow and its exceptions. Use readable Python that I can understand and explain in an interview. Proceed through the work in stages, with brief progress updates; do not stop after producing a plan. Ask only for genuinely missing information, and continue independent work when possible.
1. Inspect the workspace and starter pack
Inspect the current project and applicable repository instructions first. The starter pack may be present as client-ai-starter-pack.zip, an extracted client-ai-starter-pack directory, or copied folders. Locate the supplied files rather than assuming a hardcoded path. If the ZIP is present, extract it safely without overwriting existing user work. Preserve existing code and inspect any partial implementation before changing it.
Read these files before implementing:
- The starter pack's top-level README.md and pack-version.json.
- tasks/orders/domain.md.
- tasks/orders/seed.json.
- tasks/orders/expected-seed-results.json.
- tasks/orders/request.template.json.
- skills/generate-assignment-data/SKILL.md, if present.
- ai-workflow/manifest.template.json and ai-workflow/README.template.md.
Use only the orders assignment. Preserve the supplied rules, seed IDs, order-reference relationships, and expected seed results. Copy the relevant original material into the submission repository. The bundled data-generation skill is for preparing fixtures; follow its instructions for that stage. It does not implement or grade the application.
The supplied catalog is:
- CAB-1: USB-C cable 1 m; unit_cents = 2000.
- CAB-2: USB-C cable 2 m; unit_cents = 3000.
- HUB-1: USB hub; unit_cents = 5000.
The supplied requests are:
- R1, order_ref O1: “Please send 2 individual CAB-1 cables.” Expected draft: CAB-1, quantity 2, total_cents 4000.
- R2, order_ref O2: “Please send one Moon adapter.” Expected: needs clarification because the product is unknown.
- R3, order_ref O3: “Send two boxes of the usual cable.” Expected: needs clarification because both product and individual-item quantity are ambiguous.
- R4, order_ref O1: same text as R1. Expected: duplicate of the existing order; zero additional drafts.
Treat the actual files as authoritative if they differ from this summary. The request ID identifies an incoming request; order_ref identifies the order. Order references are available as input metadata and must not be invented by the model.
2. Use a compact implementation
Use Python 3.12, Streamlit, SQLite, Pydantic, the OpenAI Python SDK, and pytest. SQLite can use Python's standard-library sqlite3 module. Keep processing and storage logic separate from Streamlit rendering. A reasonable module split is loader, catalog, llm, validation, storage, processing, and app; adapt it if existing code makes another structure clearer.
Create a dependency file and record tested package versions. Provide Windows PowerShell and macOS/Linux setup instructions. Run locally using python -m streamlit run app.py.
The application model should default to gpt-4.1-mini, configurable through OPENAI_MODEL. Prefer an existing model/provider approved by the hiring team if the workspace establishes one. Keep the coding assistant's model separate from the application's model in documentation.
Use OPENAI_API_KEY from local environment configuration; never print, commit, or include the key in recordings. Create a sanitized .env.example and an appropriate .gitignore. Never change global AI configuration. Use applicable tool permissions normally; do not bypass approval controls.
3. Prepare and independently check the data
Keep the four original requests and extend to around ten using the supplied catalog and request template. Spend approximately 30–60 minutes on data preparation. Save additions as fixed fictional fixtures and explain their generation method. Preserve the original seeds as separate source files.
Useful additions include nine CAB-1 items, ten CAB-1 items, ten CAB-2 items, two HUB-1 items, an unambiguous description-based match, and a request missing its quantity that a reviewer later corrects. Include a multi-line order among the additions if it fits without expanding the fixture count unnecessarily.
Make the email-style requests inspectable as local text files, with a manifest preserving each id and order_ref. Retain the original JSON and document the mapping. Include source filenames in processing records.
Save expected behavior separately from application output. Independently verify at least five reference scenarios covering normal, unknown product, ambiguous quantity, duplicate, and reviewer correction. Use source inspection and separate arithmetic, not the application's pricing function, to establish the answer key. Ask me to inspect these five expectations, while continuing implementation. Label intentionally invalid fixtures. Do not invent box sizes, extra products, charges, or pricing policies.
4. Implement validation and prices in ordinary code
Read the catalog from the supplied data. Validate individual-item quantities as positive integers; reject booleans, zero, negatives, and fractional quantities. Preserve null/unresolved quantities as unresolved. “Two boxes” must not become quantity 2 individual items.
Use integer cents. For each order line, subtotal = quantity * catalog unit_cents. A line with quantity >= 10 gets a 10% discount on that line only. Round the discount half up to an integer cent. For a positive integer subtotal, (subtotal_cents + 5) // 10 implements this rounding. Sum validated line totals for the order. Distinguish any known subtotal from a complete total when some lines remain unresolved.
Verify 2 CAB-1 = 4000 cents; 9 CAB-1 = 18000; 10 CAB-1 = 18000; 10 CAB-2 = 27000; 12 CAB-1 = 21600. Test discount boundaries and multi-line calculation. Any synthetic rounding inputs used in unit tests must be identified as test-only and must not alter the supplied catalog.
5. Build real model integration and catalog tool calling
Implement a local search_catalog function exposed to the model as a function tool. It must search only the supplied catalog, validate its arguments, and return bounded catalog candidates. Match exact SKUs or unambiguous descriptions. Similarity may suggest candidates for review, but must not silently resolve an unknown product to an unrelated item.
Implement the actual tool loop: send request metadata/text and instructions to the model; receive catalog tool calls; execute the local function; return tool results to the model; obtain a structured draft. Use the OpenAI Responses API and current SDK documentation. Use strict schemas where supported and validate the final result with Pydantic. Bound the number of tool rounds and configure timeouts and limited retries.
The draft should include proposed line items, nullable SKU and quantity, source quotes, lookup evidence, unresolved issues, and a clarification draft. Save enough catalog results and source evidence to explain each proposed match. Check that cited source quotes occur in the original input and proposed SKUs exist in the catalog and are supported by lookup evidence. Correct JSON structure alone does not establish factual correctness.
Tell the model to treat customer text as data, use the supplied rules, call the catalog tool for proposed products, and preserve uncertainty. Python computes authoritative prices. Clarification text stays a local draft.
Preserve originals and show explicit processing failures for malformed files, invalid model output, refusals, incomplete responses, timeouts, unavailable models, or invalid tool arguments. An individual failure must not terminate the batch. Product/quantity ambiguity should be a needs-clarification result, distinguishable from a technical processing failure.
6. Persist state and enforce duplicate handling
Use SQLite for original requests, current orders and lines, validation findings, clarification drafts, processing attempts, and correction history. The schema can be compact; use transactions and database constraints where appropriate.
R1 and R4 must link to one order even though they have different request IDs. Reprocessing an identical request must not create another draft, inflate order counts, or overwrite human corrections. Keep the duplicate request visible with a link to the existing order. Failed attempts can be retried without creating extra orders.
If an existing order_ref arrives with changed content, preserve the new request and flag it for review. Document this chosen behavior because automatic merging is not defined by the supplied rules.
Keep validation status separate from human review status. A valid draft is ready for review; it is reviewed only after an explicit person action. If reviewed fields are subsequently changed, revalidate and require review again. Record field changes, previous/new values, actor, and timestamp.
7. Build the operations screen
Create one clear, responsive Streamlit screen with:
- Load/process sample requests and retry failed requests.
- Visible live/replay mode.
- Summary counts for ready-for-review, needs-clarification, and processing-failed requests, plus duplicate count and distinct-order count. Keep counting definitions clear.
- A request queue with status filter and selection.
- Original request beside the proposed order.
- Catalog evidence, failed checks, clarification draft, and prices formatted in USD.
- Editable SKU and quantity fields.
- Save & revalidate, followed by a separate Mark reviewed action.
- Saved revision history showing what changed.
Saving a correction reruns validation and pricing without requiring another model call. All saved changes must survive closing and restarting the app. Streamlit session state alone is insufficient. Show common exception reasons and document one intake improvement supported by actual results.
8. Record real calls and provide honest replay
Live mode makes real model calls and saves sanitized responses, relevant request/configuration metadata, tool-call arguments, and catalog tool results. Retain the actual model identifier and request timestamps. Avoid recording secrets or authorization headers.
Replay mode runs without an API key, loading saved responses from real calls and using the same validation, pricing, persistence, and review code. Match recordings using the request and relevant prompt/catalog/model/schema configuration. Label replayed responses visibly. A missing or mismatched recording must produce a clear error.
Test doubles and simulated failures must be distinctly labeled and never presented as real-call recordings. If approved API credentials are unavailable, complete all independent work, implement the live/replay paths, and report the live-call and real-replay demonstrations as pending. Ask me to configure access locally without requesting the secret in chat. Never fabricate successful calls, recordings, checks, or completion evidence.
9. Verify the required demonstrations
Produce a report showing expected and observed behavior for:
1. R1 produces CAB-1, quantity 2, total 4000 cents.
2. R2 leaves the product unresolved and creates a useful clarification.
3. R3 leaves product and individual-item quantity unresolved.
4. R4 and repeated processing do not create a second O1 order.
5. A reviewer correction changes the saved proposal, reruns checks, computes the correct price, and remains visible after an application restart.
Test technical failure isolation, correction preservation, and replay without credentials. Use clearly identified test doubles for unit tests and separately record real integration results. Verify the interactive correction flow, not just that the web server starts. Run appropriate checks and fix failures; explain unresolved limitations. Do not turn unexecuted checks into passes.
10. Complete documentation and AI workflow records
Complete and rename the supplied ai-workflow templates to manifest.json and README.md. Retain the template structure where practical and replace placeholders with actual records. Document development tools and application models separately, relevant versions/defaults/settings, prompts/rules, configuration paths, permissions, and environment variable names. Preserve earlier relevant configurations through Git history or named snapshots. Record used, default, not-used, redacted, or not-exportable accurately; do not invent tool/model versions or create unnecessary agents, skills, hooks, or MCP servers to fill categories.
Save exercise-specific instructions and prompts used during development and by the application. If this prompt is available, preserve it as a development instruction. Explain redactions without exposing their values. Document one actual AI instruction, how its output was checked, and an actual correction or improvement. Full chat logs are unnecessary.
Write a main README with setup/run/test commands, architecture, data mapping, model configuration, live/replay usage, assumptions, time spent, and limitations. Track observed time honestly; label estimates. Add a short written walkthrough suitable as the required presentation. Show normal, unclear, duplicate, and reviewer-correction behavior and explain one evidence-based intake improvement.
11. Working order and final handoff
Build in this order: inspect files; prepare checked fixtures; loader/catalog/pricing; model integration; persistence/deduplication; review UI; recording/replay; verification; documentation. Keep progress updates concise and explain decisions in language a junior Python engineer can follow.
Keep the scope local. Inventory allocation, payments, delivery scheduling, live email, ERP integration, authentication, deployment, image attachments, and elaborate infrastructure are outside the required scope. Finish the required flow before considering optional improvements.
At the end, give me the commands to run the application and checks, the main files and their responsibilities, actual verification results, pending steps requiring my input, and limitations. Be prepared to walk me through the submitted code. Do not publish, push, or send the work to the hiring team unless I explicitly ask.
