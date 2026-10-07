# Follow-up development instructions (same session, 2026-10-07)

Given to the coding assistant (Claude Code) after the brief in `dev-prompt.md`, in
this order. Saved verbatim; the assistant's replies are not included.

1. > I configured OPENAI_API_KEY locally in .env.
   >
   > Ensure the application loads .env, and verify that .env is ignored by Git. Do not display the key or the contents of .env.
   >
   > First, process only R1 using a real model call and the catalog-search tool. Save the real model response and tool-call evidence. Verify that the resulting draft contains CAB-1, quantity 2, and a calculated total of 4000 cents.
   >
   > If this succeeds, run the remaining sample requests and the required checks. Use the project's existing commands and report any errors accurately.

   (The first attempt failed with HTTP 429 `insufficient_quota`; see `README.md`.)

2. > Try again

3. > Yeah, go ahead

   (Approval to fix the duplicate-status bug and set up the virtual environment.)

4. > go on with the bug

   (Approval to fix changed-request handling and review-status reset.)

5. > Finish the remaining assignment requirements using the existing implementation. Avoid a broad rewrite or API migration solely to satisfy earlier technology suggestions.
   >
   > 1. Harden the current model integration.
   > Validate final model output and catalog-tool arguments using Pydantic or a clear equivalent validator. Handle refusals, truncated/incomplete responses, missing output, malformed JSON, and invalid tool arguments as visible processing failures. Preserve the original request and let other requests continue. Never turn an incomplete response into a valid draft.
   >
   > 2. Check catalog-tool evidence.
   > A resolved SKU must have supporting catalog lookup evidence. Inspect the existing real recordings to verify this.
   > For R3, search the catalog for the ambiguous cable description and preserve the ambiguity between cable products and the unknown individual-item quantity. If another real call is needed, make a bounded call and save it accurately. Do not invent tool calls in existing recordings.
   > A request correctly left unresolved without lookup evidence must not be presented as a confirmed catalog match.
   >
   > 3. Fix recording/replay matching.
   > Include relevant request, prompt, model, catalog, and output-schema configuration in the recording fingerprint. Preserve existing real recordings.
   > For older recordings, document their format and establish their matching configuration from available evidence. Do not claim unknown historical configuration as verified. Report missing or mismatched recordings clearly.
   >
   > 4. Update documentation from actual results.
   > Replace stale statements about missing recordings and test counts. Report the latest actual test count.
   > Document live checks separately from offline tests and Streamlit AppTest checks. Include expected versus observed results for R1, R2, R3, R4, and the R10 correction.
   > Preserve the explanation of changed-request handling as an implementation choice.
   >
   > 5. Complete AI-workflow records.
   > Save the original development prompt as ai-workflow/dev-prompt.md if it is available in this session. If unavailable, document that honestly instead of inventing the original.
   > Record actual tools, model IDs, versions, prompts, settings, and one real verification/correction example. Mark unavailable details and unused components accurately.
   >
   > 6. Initialize Git and prepare the submission.
   > Verify .gitignore before staging. Exclude .env, credentials, dependency folders, and temporary databases. Include the required source data, checked expectations, real recordings, configuration examples, tests, and documentation.
   > Check staged files for accidental secrets. Do not push or publish.
   >
   > 7. Run final targeted verification.
   > Check the normal order, unknown product, ambiguous quantity, duplicate prevention, reviewer correction persistence, and replay without credentials. Run relevant tests after changes.
   > Give me exact Windows commands to launch the app and replay, and a final checklist showing verified, failed, or unverified requirements.
   >
   > Prioritize required behavior and accurate evidence. Leave optional enhancements out. Explain any unresolved limitation.
