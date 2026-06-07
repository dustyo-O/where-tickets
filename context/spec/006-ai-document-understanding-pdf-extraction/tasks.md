# Task List: AI Document Understanding — PDF Extraction

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Technical Considerations:** [`./technical-considerations.md`](./technical-considerations.md)
- **Status:** Draft

---

## Slice 1 — Package skeleton + no-op extractor wired into the corpus runner

*Value: `just test-pdf-corpus` no longer shows the "extractor not wired" banner. It runs the full 150-scenario corpus, calls a real (stub) `where_tickets.extraction.extract_pdf`, and produces a real (0%) accuracy number. Everything is now plumbed; the rest is filling in behaviour.*

- [x] **Slice 1: Package skeleton + no-op extractor wired into the corpus runner**
  - [x] Create `backend/where_tickets/__init__.py` (empty) and `backend/where_tickets/extraction/__init__.py` re-exporting `extract_pdf`, `ExtractedFields`, `ExtractionFailedError`. **[Agent: python-backend]**
  - [x] Add a new `extraction` optional dep group to `backend/pyproject.toml`: `extraction = ["anthropic[bedrock]", "pymupdf", "jsonschema"]`. Leave the default runtime deps unchanged. **[Agent: python-backend]**
  - [x] Implement a minimal `backend/where_tickets/extraction/extract.py` with: `ExtractionFailedError` exception, an `ExtractedFields`-shaped TypedDict (or import from the runner), and a stub `extract_pdf(pdf_path: Path) -> ExtractedFields` that always raises `ExtractionFailedError("not implemented yet")`. This is enough for the runner to import and report FAIL on every scenario. **[Agent: python-backend]**
  - [x] Update `justfile`'s `test-pdf-corpus` recipe to `cd backend && PYTHONPATH=.. uv run --group extraction --group corpus python ../corpus/pdf/runner.py` so `where_tickets.extraction` resolves. **[Agent: python-backend]**
    - **Deviation logged:** actual recipe is `PYTHONPATH=. uv run --isolated --group extraction --group corpus python ../corpus/pdf/runner.py`. `PYTHONPATH=.` (not `..`) is what makes `where_tickets` resolve when CWD is `backend/`. `--isolated` keeps `anthropic[bedrock]` out of the persistent backend venv (it surfaces pre-existing pyright errors in `backend/spikes/route_engine_llm/bedrock_client.py` that break `just lint`). See follow-up note at end of file.
  - [x] Add `backend/tests/extraction/__init__.py` and `backend/tests/extraction/test_smoke.py` that imports `extract_pdf` and asserts `ExtractionFailedError` is raised on a tiny throwaway PDF. **[Agent: python-backend]**
  - [x] **Verify:** `just lint` and `cd backend && uv run pytest tests/extraction` pass. `just test-pdf-corpus` runs without the "extractor not wired" banner, reports 0 / 150 PASS for Layer 1, and the diff section shows every scenario failed with the "not implemented yet" reason. `just test` (full backend tests) still passes. **[Agent: python-backend]**

---

## Slice 2 — PDF helpers (text extraction + page→JPEG render)

*Value: deterministic, unit-tested low-level helpers exist; the extractor is one step closer to real. Nothing user-facing changes yet.*

- [x] **Slice 2: PDF helpers**
  - [x] Implement `backend/where_tickets/extraction/pdf.py`: `extract_text(pdf_path) -> str` (joins all pages, returns empty string when nothing extractable) and `render_pages_to_jpeg(pdf_path, dpi=120, quality=80) -> list[bytes]`. Constants for DPI/quality/empty-text floor live at module top. **[Agent: python-backend]**
  - [x] Add `backend/tests/extraction/test_pdf_helpers.py` using committed Layer 1 fixtures: assert `extract_text` returns non-empty on a `pdf_kind: text` scenario and empty/whitespace on a `pdf_kind: rasterized` scenario; assert `render_pages_to_jpeg` returns one JPEG per page with decodable bytes (Pillow available transitively via PyMuPDF, or use PyMuPDF's own decoder for the sanity check). **[Agent: python-backend]**
  - [x] **Verify:** `cd backend && uv run --group extraction pytest tests/extraction/test_pdf_helpers.py` passes. `just lint` clean. `just test-pdf-corpus` still runs (still 0 / 150 — extractor still stubbed). **[Agent: python-backend]**

---

## Slice 3 — Schema validator + tool-schema derivation

*Value: every model payload that ever flows back through `extract_pdf` is validated against the corpus schema. The tool `input_schema` used for forced tool-use is derived from the same JSON schema — there is exactly one source of truth.*

- [x] **Slice 3: Schema validator + tool-schema derivation**
  - [x] Implement `backend/where_tickets/extraction/schema.py`: load `corpus/pdf/schema/expected-fields.schema.json` once; expose `validate(payload) -> tuple[bool, list[str]]`. Strip `scenario_id` and `noise_seed` from the required list (extractor doesn't know corpus metadata). **[Agent: python-backend]**
  - [x] In `backend/where_tickets/extraction/prompts.py`, derive `TOOL_EMIT_EXTRACTED_FIELDS["input_schema"]` from the same loaded JSON schema (same trimming as `schema.py`). Define `TOOL_REPORT_NO_USEFUL_INFORMATION` with `{ "reason": string }`. **[Agent: bedrock-llm]**
  - [x] Draft `SYSTEM_PROMPT_TEXT` (for first Haiku-on-text call; allows either tool) and `SYSTEM_PROMPT_VISION` (Sonnet vision OCR-to-plain-text). Keep both static — they will be cached. **[Agent: bedrock-llm]**
  - [x] `backend/tests/extraction/test_schema_contract.py`: a hand-built valid payload validates clean; a payload missing a required field fails; a payload with the wrong `document_type` enum fails; assert the derived tool `input_schema` does not require `scenario_id`. **[Agent: python-backend]**
  - [x] **Verify:** `cd backend && uv run --group extraction pytest tests/extraction/test_schema_contract.py` passes. `just lint` clean. **[Agent: python-backend]**

---

## Slice 4 — Bedrock client wrapper + injectable fake

*Value: a tested abstraction sits between the extractor and `anthropic`. All future slices can use the fake in CI; live work happens against the real wrapper.*

- [x] **Slice 4: Bedrock client wrapper + injectable fake**
  - [x] Implement `backend/where_tickets/extraction/bedrock.py`: `Usage` + `ToolUseResult` dataclasses; `BedrockExtractionClient` Protocol with `complete_text(model_alias, system, user_text, tools, tool_choice) -> ToolUseResult` and `complete_vision(model_alias, system, images, prompt) -> str`; `AnthropicBedrockExtractionClient` concrete impl with lazy `anthropic` import (`TYPE_CHECKING` guard for pyright). Env overrides: `WT_BEDROCK_MODEL_HAIKU`, `WT_BEDROCK_MODEL_SONNET`. Region from `AWS_REGION`. `temperature=0`, `max_retries=4`. Apply `cache_control` on system + tools. **[Agent: bedrock-llm]**
  - [x] Add `make_client()` factory mirroring the spike's pattern: clear `ImportError` message when the `extraction` group isn't installed. **[Agent: bedrock-llm]**
  - [x] Add a `FakeBedrockExtractionClient` test helper under `backend/tests/extraction/fakes.py`: records calls, returns canned `ToolUseResult`s / vision strings in declared order, raises a clear error when the script is exhausted. **[Agent: python-backend]**
  - [x] `backend/tests/extraction/test_bedrock_wrapper.py`: model-id resolution honors env overrides; missing env falls back to the documented EU profile; `make_client` raises a clear `ImportError` with install hint when `anthropic` is absent (simulate via `sys.modules`). No live Bedrock. **[Agent: python-backend]**
  - [x] **Verify:** `cd backend && uv run pytest tests/extraction/test_bedrock_wrapper.py` passes WITHOUT the `extraction` group (the lazy-import contract). Then `uv run --group extraction pytest tests/extraction` passes. `just lint` clean. **[Agent: python-backend]**

---

## Slice 5 — Path A only (Haiku-on-text happy path)

*Value: text-bearing PDFs now extract for real. `just test-pdf-corpus` reports a real accuracy number against the ~85% non-rasterized scenarios (rasterized scenarios still FAIL — empty text → vision path doesn't exist yet, so they fall through to `ExtractionFailedError`).*

- [x] **Slice 5: Path A — Haiku-on-text happy path**
  - [x] Replace the Slice 1 stub `extract_pdf`: read text via `pdf.extract_text`; on empty text, raise `ExtractionFailedError("empty text; vision path not implemented")` (temporary; Slice 6 fills this in). On non-empty text, build the Haiku call (system + tool schemas cached, user message = text), call `complete_text`, check `tool_name`. If `emit_extracted_fields` + schema-valid → `_tag(payload, extraction_path="text")` and return. On sentinel → raise `ExtractionFailedError("sentinel; vision path not implemented")`. On invalid → raise `ExtractionFailedError("schema fail; sonnet fallback not implemented")`. **[Agent: bedrock-llm]**
  - [x] Inject the Bedrock client via a module-level factory (default: `make_client`) so tests swap in `FakeBedrockExtractionClient`. **[Agent: python-backend]**
  - [x] Emit the structured JSON log line per §2.7 on every call (success or failure). Use stdlib `logging`. **[Agent: python-backend]**
  - [x] `backend/tests/extraction/test_extract_text_path.py`: fake returns valid payload → `extract_pdf` returns it with `extraction_path="text"`; fake returns sentinel → `ExtractionFailedError` (the "vision not implemented" reason); fake returns invalid → `ExtractionFailedError` (the "sonnet not implemented" reason); log line is emitted with the expected fields. **[Agent: python-backend]**
  - [x] **Verify:** `cd backend && uv run --group extraction pytest tests/extraction` passes. `just test-pdf-corpus` reports a real PASS count for text-bearing scenarios and FAIL with `extraction_path` not yet tagged on rasterized ones; the runner's path-mix line shows mostly `text`. `just lint` clean. **[Agent: python-backend]**

---

## Slice 6 — Path B (Sonnet vision → Haiku text)

*Value: rasterized scenarios now extract for real too. `just test-pdf-corpus` accuracy jumps to cover the full 150 scenarios; the runner's path-mix line shows text + vision split that matches the corpus's `pdf_kind` mix.*

- [x] **Slice 6: Path B — Sonnet vision → Haiku text**
  - [x] In `extract_pdf`: replace both "vision not implemented" raises (empty text + sentinel) with the documented PATH B. Render JPEGs via `pdf.render_pages_to_jpeg` (single multi-image Sonnet call per §2.2 question 1); pass `raw_text` into a Haiku call with only `emit_extracted_fields` exposed. Validate, then `_tag(payload, extraction_path="vision")`. On schema fail → `ExtractionFailedError("vision path produced invalid payload")`. **[Agent: bedrock-llm]**
  - [x] Extend the structured log line to record `model_path` (one of `haiku-text`, `sonnet-vision+haiku-text`, `sonnet-text`, `failed`) and `sentinel_fired`. **[Agent: python-backend]**
  - [x] `backend/tests/extraction/test_extract_vision_path.py`: empty-text + sentinel both route via vision; fake `complete_vision` returns canned plain text; subsequent `complete_text` returns valid payload → returned with `extraction_path="vision"`; vision-leg Haiku response that fails schema raises `ExtractionFailedError`. **[Agent: python-backend]**
  - [x] **Verify:** `cd backend && uv run --group extraction pytest tests/extraction` passes. `just test-pdf-corpus` runs full 150 scenarios; path-mix line shows roughly the corpus's text vs. rasterized split (~85/15); accuracy is a real number for both paths. **[Agent: python-backend]**

---

## Slice 7 — Path C (Sonnet-on-text fallback) + total-failure path

*Value: malformed-JSON / schema-mismatch responses from Haiku no longer cost the scenario — they upgrade to Sonnet on the same text. `ExtractionFailedError` is now reserved for genuinely unreadable PDFs (the "couldn't be read" signal the spec promises).*

- [x] **Slice 7: Path C + total-failure path**
  - [x] In `extract_pdf`: replace the "sonnet fallback not implemented" branch with the documented PATH C (Sonnet `complete_text` with `emit_extracted_fields` only). Validate, then `_tag(payload, extraction_path="text")`. On invalid → `ExtractionFailedError("sonnet text fallback produced invalid payload")`. Confirm the path-trigger taxonomy is mutually exclusive (no path falls into another's trigger). **[Agent: bedrock-llm]** _(landed in Slice 6 commit during the `_run_vision_path` refactor — scope overreach, but coherent.)_
  - [x] `backend/tests/extraction/test_extract_sonnet_fallback.py`: invalid-payload Haiku → Sonnet text → valid payload → returned with `extraction_path="text"`; Sonnet-text returns invalid too → `ExtractionFailedError`. **[Agent: python-backend]**
  - [x] `backend/tests/extraction/test_extract_failure.py`: assemble the worst case (Haiku invalid → Sonnet text invalid) and an empty-text worst case (vision → invalid Haiku); both raise `ExtractionFailedError` and log `model_path="failed"`. **[Agent: python-backend]**
  - [x] **Verify:** `cd backend && uv run --group extraction pytest tests/extraction` passes. `just lint` clean. `just test-pdf-corpus` accuracy improves (Path C catches schema flakes). **[Agent: python-backend]** _(Held at 142/150 — none of the 8 remaining failures are schema-invalid; they're all bus-return stations-length-mismatch cases that produce valid payloads. Slice 9 territory.)_

---

## Slice 8 — Debug CLI (`just extract-pdf <path>`)

*Value: the engineer can inspect any single PDF on demand and see the chosen path + the returned fields, without re-running the whole corpus.*

- [ ] **Slice 8: Debug CLI**
  - [ ] Implement `backend/where_tickets/extraction/cli.py` and `backend/where_tickets/extraction/__main__.py`: `python -m where_tickets.extraction <pdf-path>` runs `extract_pdf`, prints the result as JSON (pretty), prints the chosen `extraction_path` + `model_path` on stderr, exits 0; on `ExtractionFailedError`, prints the reason on stderr and exits 1. **[Agent: python-backend]**
  - [ ] Add `extract-pdf path` recipe to `justfile`: `cd backend && uv run --group extraction python -m where_tickets.extraction {{path}}`. **[Agent: python-backend]**
  - [ ] `backend/tests/extraction/test_cli.py`: with `FakeBedrockExtractionClient` (wired in via the same module-level factory swap used by the other tests), assert exit 0 + JSON on success and exit 1 + diagnostic on `ExtractionFailedError`. **[Agent: python-backend]**
  - [ ] **Verify:** `cd backend && uv run --group extraction pytest tests/extraction/test_cli.py` passes. `just lint` clean. **[Agent: python-backend]**

---

## Slice 9 — Live accuracy gate (≥ 99% on Layer 1)

*Value: the spec's hard gate is met. This is the "Done" check.*

- [ ] **Slice 9: Live accuracy gate**
  - [ ] Confirm `wt-bedrock` SSO profile + `eu-north-1` access is configured locally; document the one-line setup in `backend/where_tickets/extraction/README.md` (short: env vars, SSO login, `just extract-pdf` smoke). **[Agent: bedrock-llm]**
  - [ ] Run `just test-pdf-corpus` live against the 150 Layer 1 scenarios. Iterate on `SYSTEM_PROMPT_TEXT` / `SYSTEM_PROMPT_VISION` until overall accuracy ≥ 99%. Use the runner's per-failure diff and the path-mix line to drive the iteration. Capture cost/latency from the structured log lines and note them in the README for posterity. **[Agent: bedrock-llm]**
  - [ ] If any scenario keeps failing despite prompt iteration, decide and document: either fix the prompt / tool schema, or capture the case as a known-limitation note in the README. Do not silently exclude scenarios. **[Agent: bedrock-llm]**
  - [ ] **Verify:** `just test-pdf-corpus` reports ≥ 99% Layer 1 accuracy. Path-mix line shows rasterized scenarios on `vision` and text scenarios on `text` (no accidental cross-overs). `just ci-backend` is green. **[Agent: bedrock-llm]**

---

## Scope adjustment — QR/barcode extraction deferred to DUS-33

After Slice 5 surfaced that the only consistent text-path failure was the model returning `qr_codes: []` while the corpus expected payloads, QR/barcode extraction was moved out of this ticket. Reading a barcode requires decoding the image region (not reading a text label), and the LLM-side "infer from nearby text" approach risks hallucination — see DUS-33 for the deferred work.

Changes made (commit on this branch):
- Both system prompts in `backend/where_tickets/extraction/prompts.py` now instruct the model NOT to extract QR/barcode payloads (text path emits `qr_codes: []` always; vision path ignores QR/barcode regions entirely).
- `corpus/pdf/runner.py`'s `compare()` temporarily skips the `qr_codes` field. The corpus's expected payloads are kept intact so DUS-33 can re-enable the check trivially.
- The functional + technical specs were updated to reflect the new scope.

## Follow-up notes

- **Pre-existing pyright errors in `backend/spikes/route_engine_llm/bedrock_client.py`** surface as soon as `anthropic[bedrock]` is installed in the persistent backend venv. Slice 1 sidesteps this by running `test-pdf-corpus` with `uv run --isolated`. **Before Slice 5** (which is the first slice that imports `anthropic` from production code), either: (a) fix the spike's pyright issues, or (b) move the spike under a pyright-exclusion path. Otherwise `just lint` will break the moment the `extraction` group lands in the dev venv.
- **Bedrock tool-schema risk (Slice 5/6).** The corpus schema uses `unevaluatedProperties: false` inside `allOf` on `stations`/`accommodations`/`venues`, and Bedrock's pre-flight tool-schema validation may reject that. If the first live call in Slice 5/6 rejects the schema, the fix is either to flatten the `allOf` (inline the per-bucket datetime fragments into each items shape and drop `unevaluatedProperties`) or replace `unevaluatedProperties: false` with `additionalProperties: false`. The single source of truth lives in the corpus tree, so any structural change is deliberate — don't pre-flatten.
