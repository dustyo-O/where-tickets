# Technical Specification: AI Document Understanding — PDF Extraction

- **Functional Specification:** [`functional-spec.md`](./functional-spec.md)
- **Status:** Completed
- **Author(s):** Dusty

---

## 1. High-Level Technical Approach

Introduce a new production-code package `where_tickets.extraction` under `backend/`, exposing a synchronous function `extract_pdf(pdf_path: Path) -> ExtractedFields`. This is the callable the existing DUS-30 PDF-corpus runner imports by default (`corpus/pdf/runner.py:557`); wiring it in turns `just test-pdf-corpus` into a real accuracy gate against the 150 Layer 1 scenarios.

The control flow follows the DUS-32 algorithm verbatim — Haiku-on-printed-text primary, with two mutually-exclusive fallbacks (Sonnet vision when text is empty or Haiku returns the "no useful information" sentinel; Sonnet-on-text when Haiku produced malformed JSON or a schema mismatch). When every path fails, the function raises `ExtractionFailedError`; the runner records the scenario as FAIL with reason, and the eventual production caller (Lambda) translates the exception into the "couldn't be read" signal the functional spec requires.

A Bedrock-access wrapper (`bedrock.py`) mirrors the LLM engine spike's pattern: an injectable client protocol, lazy import of `anthropic`, EU inference-profile defaults with env overrides, prompt caching on the stable system + tool-schema prefix. `anthropic[bedrock]` + `pymupdf` + `jsonschema` live in a new optional `extraction` dep group (default-runtime FastAPI image stays lean).

A new `just extract-pdf <path>` recipe wraps a one-PDF debug invocation. `just test-pdf-corpus` is updated to wire the dep group + `PYTHONPATH` so the runner can import `where_tickets.extraction.extract_pdf`.

No database changes are in scope; this slice is a pure function. The production pipeline (Lambda + SQS + S3 wiring) is owned by future slices of the roadmap line.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Module breakdown

New package, all paths relative to `backend/`:

| File | Responsibility |
|---|---|
| `where_tickets/__init__.py` | Package marker (empty). |
| `where_tickets/extraction/__init__.py` | Re-exports `extract_pdf`, `ExtractedFields`, `ExtractionFailedError`. |
| `where_tickets/extraction/extract.py` | `extract_pdf` entry point; three-path control flow; structured per-call log line. |
| `where_tickets/extraction/pdf.py` | PyMuPDF helpers: `extract_text(pdf)`, `render_pages_to_jpeg(pdf, dpi=120, quality=80)`. |
| `where_tickets/extraction/bedrock.py` | Injectable `BedrockExtractionClient` protocol + `AnthropicBedrockExtractionClient`; lazy `anthropic` import; `make_client()` factory; model-id resolution with env overrides. |
| `where_tickets/extraction/prompts.py` | System prompt (cached), the two tool schemas (`emit_extracted_fields`, `report_no_useful_information`), and a small builder for the vision-path "OCR-the-document-to-plain-text" prompt. |
| `where_tickets/extraction/schema.py` | Loads `corpus/pdf/schema/expected-fields.schema.json` once; `validate(payload) -> ExtractedFields | ValidationError`. |
| `where_tickets/extraction/cli.py` | `python -m where_tickets.extraction <pdf-path>` — backs the `just extract-pdf` recipe. |

Tests under `backend/tests/extraction/` (new directory):

| File | Coverage |
|---|---|
| `test_extract_text_path.py` | Haiku-on-text happy path returns valid `ExtractedFields`. |
| `test_extract_vision_path.py` | Empty-text and sentinel cases both route to Sonnet vision → Haiku JSON. |
| `test_extract_sonnet_fallback.py` | Malformed Haiku JSON and schema-fail both route to Sonnet text. |
| `test_extract_failure.py` | Every path fails → `ExtractionFailedError`. |
| `test_schema_contract.py` | Every fake client response that ever ends up returned validates against `expected-fields.schema.json`. |
| `test_pdf_helpers.py` | PyMuPDF wrappers: empty-text detection, page count, JPEG bytes round-trip. |
| `test_cli.py` | CLI prints JSON + chosen path; non-zero exit on `ExtractionFailedError`. |

The injected `BedrockExtractionClient` fake records calls and returns canned `ToolUseResult`s; **no live Bedrock in CI**. Live accuracy comes from the engineer-on-demand corpus run.

### 2.2 Algorithm and data flow

```text
extract_pdf(pdf_path) → ExtractedFields
│
├── text = pdf.extract_text(pdf_path)
│
├── if text is non-empty:                           # PATH A: text → Haiku
│       result = bedrock.complete_text(
│           model="haiku", text=text, tools=[emit_extracted_fields, report_no_useful_information]
│       )
│       if result.tool_name == "emit_extracted_fields" and schema.validate(result.payload):
│           return _tag(result.payload, extraction_path="text")
│       elif result.tool_name == "report_no_useful_information":
│           goto PATH B (vision)
│       else:                                       # malformed JSON / schema mismatch
│           goto PATH C (sonnet text)
│
├── else:                                            # empty text
│       goto PATH B (vision)
│
├── PATH B (vision):                                # extraction_path = "vision"
│       images = pdf.render_pages_to_jpeg(pdf_path, dpi=120, quality=80)
│       raw_text = bedrock.complete_vision(
│           model="sonnet", images=images, prompt=VISION_OCR_PROMPT
│       )
│       result = bedrock.complete_text(
│           model="haiku", text=raw_text, tools=[emit_extracted_fields]
│       )                                            # no sentinel tool on the second leg
│       if schema.validate(result.payload):
│           return _tag(result.payload, extraction_path="vision")
│       raise ExtractionFailedError("vision path produced invalid payload")
│
└── PATH C (sonnet text):                           # extraction_path = "text"
        result = bedrock.complete_text(
            model="sonnet", text=text, tools=[emit_extracted_fields]
        )
        if schema.validate(result.payload):
            return _tag(result.payload, extraction_path="text")
        raise ExtractionFailedError("sonnet text fallback produced invalid payload")
```

Path A and B are the documented happy paths. Path C is the documented "model upgrade" fallback. Any leftover failure is `ExtractionFailedError` — the runner counts this as a scenario FAIL; the eventual Lambda caller will translate it into the "couldn't be read" signal.

`_tag` strips the `scenario_id` field (the Bedrock side never produces one) and attaches `extraction_path`. `pdf_kind` comes from the model output (the schema requires it).

### 2.3 Bedrock client

Modeled on `backend/spikes/route_engine_llm/bedrock_client.py`:

- Protocol `BedrockExtractionClient` with `complete_text(model_alias, ..., tools, tool_choice="any") -> ToolUseResult` and `complete_vision(model_alias, images, prompt) -> str`.
- `AnthropicBedrockExtractionClient` is the production implementation; `anthropic` is imported **lazily** so the offline tests work without the `extraction` group.
- Model aliases: `"haiku"`, `"sonnet"`. EU inference-profile defaults (parallel to the spike's), overridable by env:
  - `WT_BEDROCK_MODEL_HAIKU` (default: `eu.anthropic.claude-haiku-4-5-20251001-v1:0`)
  - `WT_BEDROCK_MODEL_SONNET` (default: `eu.anthropic.claude-sonnet-4-6`)
- Region from `AWS_REGION`; SDK retries (4) for throttling/5xx.
- `temperature=0` for both models (determinism).
- `max_tokens` headroom: 4096 for text-path JSON output, 8192 for vision raw-text output.
- Prompt caching (`cache_control`) marks the system prompt + tool schemas as the cached prefix; per-call PDF text / images go in the user message after the cache boundary.

### 2.4 Prompts and tool schemas

**QR / barcode deferral.** Both system prompts explicitly tell the model NOT to extract QR or barcode payloads — the field is always emitted as `qr_codes: []`. Reading the actual barcode image is owned by DUS-33; this slice keeps the schema field present (so re-enabling it later is purely additive) but the comparison is also temporarily skipped in `corpus/pdf/runner.py`'s `compare()`.

`prompts.py` exposes:

- `SYSTEM_PROMPT_TEXT` — instructs Haiku/Sonnet text-path to extract every printed fact into the `emit_extracted_fields` tool, and to call `report_no_useful_information` instead when the text is travel-document-irrelevant (only on the **first** Haiku-on-text call; the vision-leg Haiku call sees only `emit_extracted_fields`).
- `SYSTEM_PROMPT_VISION` — instructs Sonnet vision to read the document and emit raw plain text; not structured.
- `TOOL_EMIT_EXTRACTED_FIELDS` — `input_schema` is derived directly from `corpus/pdf/schema/expected-fields.schema.json` (loaded once, with `scenario_id`/`noise_seed` removed; `pdf_kind` retained).
- `TOOL_REPORT_NO_USEFUL_INFORMATION` — minimal schema: `{ reason: string }`.
- Both tools sit in the cached prefix; the schema-derived `input_schema` enforces structure at the model level.

### 2.5 Schema validation

`schema.py` loads `corpus/pdf/schema/expected-fields.schema.json` once at import time using `jsonschema`. The validator:

- Removes the `scenario_id` and `noise_seed` fields from the required list (the extractor doesn't know about corpus metadata).
- Returns `(ok, errors)`; `ok = False` cascades into the documented fallbacks.
- Reused by every test that ever holds an output payload.

### 2.6 PDF helpers

`pdf.py` wraps PyMuPDF:

- `extract_text(pdf_path) -> str` — joins all pages with `\n`; empty/whitespace-only result triggers the vision path.
- `render_pages_to_jpeg(pdf_path, dpi=120, quality=80) -> list[bytes]` — one JPEG per page; matched to corpus's `pdf_kind: rasterized` render settings.
- Internal counter `_PYMUPDF_NULL_TEXT_FLOOR` (currently 8 chars) for the empty-text decision; tunable in code.

### 2.7 Observability

Per `extract_pdf` call, emit one structured JSON log line via the stdlib `logging` module:

| Field | Source |
|---|---|
| `pdf_path` | input |
| `extraction_path` | `"text"` or `"vision"` (matches returned value) |
| `model_path` | `"haiku-text"` / `"sonnet-vision+haiku-text"` / `"sonnet-text"` / `"failed"` |
| `sentinel_fired` | bool |
| `latency_ms_total` | wall-clock |
| `latency_ms_per_call` | list per Bedrock call |
| `tokens_input` / `tokens_output` / `tokens_cache_read` / `tokens_cache_creation` | summed across calls |
| `pdf_page_count` | from PyMuPDF |
| `error_reason` | when `ExtractionFailedError` is raised |

CloudWatch picks this up in production; the corpus runner just prints it. No assertions on it from the corpus.

### 2.8 Dependency packaging

Add to `backend/pyproject.toml`:

```toml
extraction = [
    "anthropic[bedrock]",
    "pymupdf",
    "jsonschema",
]
```

Kept out of default runtime deps so the FastAPI ECS image stays lean — extraction will run in Lambda. Tests covering the extraction package live under the default `dev` group (no Bedrock dep needed because the client is faked).

### 2.9 `just` recipes

Update `justfile`:

- `test-pdf-corpus` becomes:
  ```
  cd backend && PYTHONPATH=.. uv run --group extraction --group corpus python ../corpus/pdf/runner.py
  ```
  - `extraction` group brings PyMuPDF + anthropic + jsonschema.
  - `corpus` group already had PyMuPDF (kept for now; can be deduplicated later).
  - `PYTHONPATH=..` lets `corpus/pdf/runner.py` resolve while keeping CWD inside `backend/` so `where_tickets.*` imports work.
- New `extract-pdf path`:
  ```
  cd backend && uv run --group extraction python -m where_tickets.extraction.cli {{path}}
  ```

### 2.10 Where this connects to the rest of the system today vs. later

- **Today (this slice):** the extractor is reachable from `corpus/pdf/runner.py` and `just extract-pdf`. No FastAPI route, no Lambda, no S3, no SQS.
- **Later (future slices of the AI Document Understanding line):** Lambda packaging using the same `extraction` group; SQS event consumer wiring; the `ExtractionFailedError` translation to a database "couldn't-read" marker. Out of scope here.

---

## 3. Impact and Risk Analysis

### System Dependencies

- **DUS-30 (Mock-Document Corpus):** consumer. Nothing in `corpus/pdf/runner.py` changes; we just supply the default-named extractor it imports.
- **`corpus/pdf/schema/expected-fields.schema.json`:** authoritative for the output shape. Read at module import; if the schema changes, the extractor automatically tracks it.
- **DUS-31 (engine fragment schema extension):** related but **not blocking**. Once it lands, the runtime cross-schema assertion mentioned in `corpus/pdf/README.md` upgrades; this work ships first regardless.
- **No DB / migration impact.**
- **No FastAPI route changes** in this slice.

### Potential Risks & Mitigations

- **Bedrock throttling / cost on a full corpus run.** 150 PDFs × up to 3 Bedrock calls each = up to 450 calls. *Mitigation:* prompt caching on the (large) system + tool schema prefix; SDK retries with bounded exponential backoff (`max_retries=4`); sequential execution by default; per-call usage logged so the cost is observable.
- **Failure to hit ≥ 99 % accuracy on first wiring.** *Mitigation:* the corpus runner's per-failure expected-vs-actual diff already exists; iteration on prompt + tool schema is expected. The path-mix telemetry will quickly surface if rasterized scenarios are accidentally going to text or text scenarios falling back to vision.
- **DPI drift between corpus rasterization and extractor rendering.** Both use 120 DPI today; if either changes, accuracy may swing. *Mitigation:* the DPI constant lives in one place (`pdf.py`) and matches the corpus's rasterizer; cross-reference noted in code.
- **Schema drift between `expected-fields.schema.json` and what the model emits.** *Mitigation:* the tool `input_schema` IS the corpus schema (minus corpus-only metadata) — derived, not hand-maintained. Every returned payload is validated; mismatches trigger Path C / failure.
- **`anthropic` install bloat on local dev that doesn't run extraction.** *Mitigation:* optional `extraction` dep group; tests use the faked client and need nothing.
- **Multi-page PDFs blowing the Sonnet vision token budget.** *Mitigation:* 120 DPI / quality 80 is the same setting the corpus rasterizes at and was sized for these layouts; `max_tokens=8192` headroom on the vision call. Pathological page counts (e.g. 20-page boarding-pass dumps) are out of v1 scope.
- **`extract_pdf` is synchronous; FastAPI/Lambda integration will need its own concurrency story.** Out of scope here; flagged for the Lambda-packaging slice.

---

## 4. Testing Strategy

- **Unit tests (CI, no live Bedrock):** per-branch coverage of the control flow using a `FakeBedrockExtractionClient` that returns canned `ToolUseResult`s in the order the test expects.
  - Happy text path → Haiku returns valid payload → returned as `extraction_path="text"`.
  - Sentinel path → Haiku returns `report_no_useful_information` → vision path runs → Haiku returns valid payload → returned as `extraction_path="vision"`.
  - Schema-fail path → Haiku returns invalid payload → Sonnet-text runs → valid payload → returned as `extraction_path="text"`.
  - Empty-text path → bypasses Haiku-text → vision path runs.
  - Total-failure path → every path produces invalid → `ExtractionFailedError` raised.
- **PyMuPDF helper tests:** real (committed) Layer 1 PDFs as fixtures; assert empty-text detection on a rasterized scenario, non-empty on a text scenario; assert JPEG bytes are decodable at expected dimensions.
- **Schema-contract test:** every test that holds a returned payload runs it through `schema.validate` to guarantee the post-condition.
- **CLI test:** `python -m where_tickets.extraction.cli <fixture-pdf>` returns 0 + JSON on success, non-zero with a diagnostic on `ExtractionFailedError`. Uses the fake client via env override.
- **Live integration (engineer-on-demand, NOT CI):** `just test-pdf-corpus` against the 150 Layer 1 scenarios using live Bedrock via the `wt-bedrock` SSO profile. PASS requires overall accuracy ≥ 99 %.
- **Static checks:** ruff + pyright over the new `where_tickets` package and its tests; pyright stays clean without the `extraction` group installed (lazy `anthropic` import + `TYPE_CHECKING` guard, mirroring the spike).
