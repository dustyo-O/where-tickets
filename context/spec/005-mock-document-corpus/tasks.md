# Task List: Mock-Document Corpus

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Technical Considerations:** [`./technical-considerations.md`](./technical-considerations.md)
- **Status:** Draft

---

## Slice 1 — Schema + validator + one hand-authored Layer 1 fixture

*Value: `just test-corpus` validates a real fixture against a real schema. The corpus tree exists and is wired into CI.*

- [x] **Slice 1: Schema + validator + one hand-authored Layer 1 fixture**
  - [x] Create `corpus/pdf/` tree: `README.md` (short orientation), `schema/`, `layer1/scenarios/`, `layer2/.gitkeep`. Add `corpus/pdf/layer2/**` to root `.gitignore` (with the `.gitkeep` exception). **[Agent: python-backend]**
  - [x] Write `corpus/pdf/schema/expected-fields.schema.json` (Draft 2020-12) with fields per tech spec §2.2: `document_type`, `cities[]`, `dates[]`, `times[]`, `travelers[]`, `prices[]`, `qr_codes[]`, `pdf_kind`, `scenario_id`, `noise_seed?`. **[Agent: python-backend]**
  - [x] Extend `corpus/pdf/schema/expected-fields.schema.json` with the three structured place arrays per the refined tech spec §2.2: `stations[]` (kind ∈ airport / rail_station / bus_terminal), `accommodations[]` (kind ∈ hotel / airbnb), `venues[]` (kind ∈ sightseeing / parking / other). Each entry: `{ city, kind, identifier }`. All three arrays required at schema level (`additionalProperties: false`); per-`document_type` minimum-count rules live in the validator code, not the schema. **[Agent: python-backend]**
  - [x] Hand-author one minimal fixture at `corpus/pdf/layer1/scenarios/001-fixture-air-ticket/`: a tiny PDF (any tool — fpdf or even reportlab is fine here, since the WeasyPrint generator lands in Slice 3) + matching `expected-fields.json` with `pdf_kind: text` + a one-line `README.md`. This fixture exists so the validator has something real to chew on before the generator ships. **[Agent: python-backend]**
  - [x] Write `corpus/pdf/validate.py` for per-PDF structural validation at this slice: walk every `corpus/pdf/layer1/scenarios/*/expected-fields.json` + every `corpus/pdf/layer2/**/*.expected-fields.json`, then for each file run (a) JSON Schema validation against `expected-fields.schema.json`, (b) the city-integrity rule — every `stations[].city`, `accommodations[].city`, `venues[].city` must appear in `cities[]`, (c) per-`document_type` minimum counts — `air_ticket`/`rail_ticket`/`bus_ticket` → `stations[] ≥ 2`; `hotel_booking`/`airbnb_booking` → `accommodations[] ≥ 1`; `supplementary` → no minimum. Aggregate failures, print a clear per-file report, exit non-zero on any failure. Drift / PDF-token sanity / coverage / leak-guard / cross-schema checks land in later slices. **[Agent: python-backend]**
  - [x] Extend the `test-corpus` recipe in `justfile` to also run `uv run --python 3.12 --with jsonschema python corpus/pdf/validate.py`. **[Agent: python-backend]**
  - [x] **Verify:** run `just test-corpus` — must pass and report the fixture as validated. Run `just test` — must still pass (no regression). Confirm `corpus/pdf/layer2/.gitkeep` is tracked and the rest of `layer2/` is ignored. **[Agent: python-backend]**

---

## Slice 2 — Runner CLI + stub extractor + PASS/FAIL report against the fixture

*Value: `just test-pdf-corpus` exists, discovers scenarios, calls the extractor interface, and emits the PASS/FAIL + path-mix report. With no real extractor yet, it degrades gracefully.*

- [x] **Slice 2: Runner CLI + stub extractor + PASS/FAIL report against the fixture**
  - [x] Define the extractor interface in code: a `Protocol` (or `Callable` type alias) in `corpus/pdf/runner.py` with signature `extract_pdf(pdf_path: Path) -> ExtractedFields`, where `ExtractedFields` is a TypedDict (or dataclass) matching the schema plus optional `extraction_path: Literal["text", "vision"]`. **[Agent: python-backend]**
  - [x] Write `corpus/pdf/runner.py` with `python -m corpus.pdf.runner [--layer 1|2|both] [--filter PATTERN] [--json-report PATH]`. Discovery: walk Layer 1 scenarios + Layer 2 dirs. Per-PDF flow: load PDF + JSON → call extractor → field-by-field compare → PASS/FAIL. Report shape per tech spec §2.5 (Layer 1 / Layer 2 / TOTAL counts, accuracy %, FAILED section with diff). **[Agent: python-backend]**
  - [x] Implement lazy import of `where_tickets.extraction.extract_pdf` with a graceful fallback: if the module is missing, the runner prints `extractor not wired: install per AI Document Understanding spec` and exits 0. (Missing extractor is not a corpus failure.) **[Agent: python-backend]**
  - [x] Add `just test-pdf-corpus` recipe wrapping the runner. **[Agent: python-backend]**
  - [x] Pytest test in `backend/tests/corpus/test_pdf_runner.py`: runs the runner against a tiny tempdir fixture (2 scenarios, one matching expected and one mismatching) using a **stub extractor injected via a CLI flag** (e.g., `--extractor-import-path tests.stubs.stub_extractor`). Asserts: exit code, PASS/FAIL counts, path-mix line present, diff format on the failing scenario. **[Agent: python-backend]**
  - [x] **Verify:** `just test-pdf-corpus` runs against the Slice 1 fixture and prints the "extractor not wired" banner cleanly (exit 0). `uv run pytest backend/tests/corpus/test_pdf_runner.py` passes. **[Agent: python-backend]**

---

## Slice 3 — Generator for ONE document type (air ticket) + JSON drift check + PDF/JSON sanity

*Value: `just regen-pdf-corpus` produces ~25 deterministic-data air-ticket scenarios. Validator drift-checks JSON and confirms PDF/JSON consistency. The hand-authored fixture is gone, replaced by generator output.*

- [x] **Slice 3: Generator for ONE document type (air ticket) + JSON drift check + PDF/JSON sanity**
  - [x] Add `weasyprint` and `jinja2` to the backend's `corpus` (or new `pdf-corpus`) dep group. Document `brew install pango` for macOS in `corpus/pdf/README.md`. **[Agent: python-backend]**
  - [x] Write `corpus/pdf/generator/data.py`: stable data API (city pool, dates from epoch `2027-03-01T00:00:00Z`, traveler names, prices, QR payloads) seeded from scenario axes via SHA-256. **[Agent: python-backend]**
  - [x] Write `corpus/pdf/generator/noise.py`: bounded randomized noise functions (banner count 0–2, T&C presence, footer ad variant from fixed catalog, font-pair pick from small palette). Each takes a `Random` instance. **[Agent: python-backend]**
  - [x] Write `corpus/pdf/generator/render.py`: WeasyPrint orchestration — `render_pdf(template, data, noise, out_path)`. **[Agent: python-backend]**
  - [x] Author template tree: `corpus/pdf/generator/templates/air-ticket.html.j2`, plus `templates/partials/` (ads, T&Cs, footers) and `templates/styles/` (CSS palette for one fictional airline brand). Use open-source bundled fonts (Inter or IBM Plex). **[Agent: python-backend]**
  - [x] Write `corpus/pdf/generator/matrix.py`: enumerate air-ticket scenarios across coverage axes (~25 scenarios for this slice). Stable `NNN-<slug>` IDs. **[Agent: python-backend]**
  - [x] Write `corpus/pdf/generator/__main__.py` — entry point for `python -m corpus.pdf.generator`. Removes the hand-authored fixture from Slice 1 and emits the generated air-ticket scenarios into `corpus/pdf/layer1/scenarios/`. **[Agent: python-backend]**
  - [x] Add `just regen-pdf-corpus` recipe. **[Agent: python-backend]**
  - [x] Extend `corpus/pdf/validate.py` with the **JSON drift check**: regenerate Layer 1 JSON into a tempdir (data layer only, no rendering), diff against committed `expected-fields.json`, fail on any drift. **[Agent: python-backend]**
  - [x] Extend `corpus/pdf/validate.py` with **PDF/JSON consistency sanity for `pdf_kind: text`**: PyMuPDF extracts the text layer, asserts every `cities[]` and `dates[]` token appears verbatim. **[Agent: python-backend]**
  - [x] Pytest unit test on `noise.py`: with a seeded RNG, generated noise stays within configured palettes (counts capped, fonts from pool). **[Agent: python-backend]**
  - [x] Commit the regenerated air-ticket scenarios (~25 PDFs + JSON) into `corpus/pdf/layer1/scenarios/`. **[Agent: python-backend]**
  - [x] **Verify:** `just regen-pdf-corpus` runs cleanly. Run it twice; `git diff -- '*.json'` is empty (JSON is byte-stable). `just test-corpus` passes (schema + drift + token sanity). `just test-pdf-corpus` runs against the ~25 scenarios with the "extractor not wired" banner. `uv run pytest backend/tests/corpus/` passes. **[Agent: python-backend]**

---

## Slice 4 — Extend generator to the remaining 5 document types

*Value: corpus reaches the ~50 trips / ~150 PDFs coverage target. Every document type the product accepts is represented.*

- [x] **Slice 4: Extend generator to the remaining 5 document types**
  - [x] Add `rail-ticket.html.j2` + brand styles + matrix entries. Regen. Confirm validator still passes. **[Agent: python-backend]**
  - [x] Add `bus-ticket.html.j2` + brand styles + matrix entries. Regen. Confirm validator still passes. **[Agent: python-backend]**
  - [x] Add `hotel-booking.html.j2` + brand styles + matrix entries. Regen. Confirm validator still passes. **[Agent: python-backend]**
  - [x] Add `airbnb-booking.html.j2` + brand styles + matrix entries. Regen. Confirm validator still passes. **[Agent: python-backend]**
  - [x] Add `supplementary.html.j2` (voucher / sightseeing / parking variants) + matrix entries. Regen. Confirm validator still passes. **[Agent: python-backend]**
  - [x] Commit final regenerated corpus. **[Agent: python-backend]**
  - [x] **Verify:** `just test-corpus` passes. Count: `ls corpus/pdf/layer1/scenarios/ | wc -l` is in [135, 165] (single-PDF scenarios; the "trip" framing was dropped for Layer 1 — see tech spec §2.3). At least one scenario per document type. `just test-pdf-corpus` exercises all of them. **[Agent: python-backend]**

---

## Slice 5 — Rasterized rendering axis (~15%) + path-mix reporting

*Value: ~22 of ~150 PDFs are rasterized (image-only). Validator distinguishes `text` vs `rasterized` sanity. Runner reports path-mix and the diagnostic for accidental vision fallback.*

- [x] **Slice 5: Rasterized rendering axis (~15%) + path-mix reporting**
  - [x] Extend `render.py` with a `rasterize=True` mode: take WeasyPrint PDF output, render each page to PNG at 120 DPI via PyMuPDF, re-emit as image-only PDF. **[Agent: python-backend]**
  - [x] Extend `matrix.py` to flag ~15% of scenarios as `rendering=rasterized` (deterministic selection). **[Agent: python-backend]**
  - [x] Regen and commit. ~22 scenarios now have `pdf_kind: rasterized` in their JSON and image-only PDFs. **[Agent: python-backend]**
  - [x] Extend `corpus/pdf/validate.py` PDF/JSON sanity to **split by `pdf_kind`**: `text` → existing check; `rasterized` → assert text layer is empty (whitespace-only). **[Agent: python-backend]**
  - [x] Extend `runner.py` report with the **path-mix summary** (`text=N vision=M`) per layer, computed from each result's `extraction_path`. **[Agent: python-backend]**
  - [x] Extend `runner.py` with the **DIAGNOSTIC** section listing `pdf_kind=text` scenarios where the extractor reported `extraction_path=vision` (non-failing; informational). **[Agent: python-backend]**
  - [x] Extend the stub extractor in the pytest fixtures to optionally return `extraction_path: "vision"` for rasterized PDFs, so the test covers both branches. Update `test_pdf_runner.py` to assert path-mix output and the diagnostic line. **[Agent: python-backend]**
  - [x] **Verify:** ~22 of the committed PDFs are rasterized; validator's split sanity passes both branches. `just test-pdf-corpus` shows path mix in its report. The diagnostic line appears when the stub reports a false fallback. Pytest passes. **[Agent: python-backend]**

---

## Slice 6 — Layer 2 (real-PDF) support end-to-end

*Value: a contributor can drop a real PDF + expected-fields.json into `corpus/pdf/layer2/<trip-slug>/` and the next run picks it up. Leak guard prevents accidental commits.*

- [ ] **Slice 6: Layer 2 (real-PDF) support end-to-end**
  - [ ] Extend `runner.py` Layer 2 discovery: walk `corpus/pdf/layer2/<trip>/*.pdf` + sibling `*.expected-fields.json`, validate each JSON against the schema, run the same per-PDF flow. **[Agent: python-backend]**
  - [ ] Confirm the runner's Layer 1 / Layer 2 / TOTAL split in the report works with a non-empty Layer 2. **[Agent: python-backend]**
  - [ ] Extend `corpus/pdf/validate.py` with the **layer2 leak guard**: assert `git ls-files corpus/pdf/layer2/` returns only `.gitkeep`; any other tracked path fails the validator. **[Agent: python-backend]**
  - [ ] Document the Layer 2 contributor workflow in `corpus/pdf/README.md`: directory shape, how to author `expected-fields.json`, how to determine `pdf_kind`, that PDFs must never be committed. **[Agent: python-backend]**
  - [ ] Pytest test that drops a synthetic real-PDF + JSON pair into a tempdir layer2 (with `WT_LAYER2_ROOT` env override on the runner — add the env hook), runs the runner, asserts the trip is discovered and reported under Layer 2 totals. **[Agent: python-backend]**
  - [ ] Pytest test for the leak guard: stage a fake PDF under `corpus/pdf/layer2/`, run the validator, assert it fails with a clear message. **[Agent: python-backend]**
  - [ ] **Verify:** With Layer 2 empty, `just test-pdf-corpus` and `just test-corpus` pass. With a stubbed `layer2/<trip>/foo.pdf` + `foo.expected-fields.json` in a tempdir, the runner discovers and reports it. The leak guard catches a deliberate `git add` of a fake real PDF. **[Agent: python-backend]**

---

## Slice 7 — Coverage assertions + cross-schema sanity

*Value: the corpus's own acceptance criteria are mechanically checked on every CI run. We can't accidentally drop below the required scenario shapes or break the engine's fragment-schema contract.*

- [ ] **Slice 7: Coverage assertions + cross-schema sanity**
  - [ ] Extend `corpus/pdf/validate.py` with **coverage assertions** per the functional spec:
    - Layer 1 single-PDF scenarios ∈ [135, 165]
    - ≥1 scenario per document type (all 6)
    - ≥3 multi-leg scenarios (`cities[].length ≥ 3`)
    - ≥3 multi-traveler scenarios (`travelers[].length ≥ 2`)
    - ≥3 return-ticket scenarios (tagged in scenario metadata)
    - ≥3 standalone-supplementary scenarios
    - ~15% rasterized (count ∈ [18, 28])
    - **[Agent: python-backend]**
  - [ ] Extend `corpus/pdf/validate.py` with **cross-schema sanity**: load `corpus/schema/extracted-fragment.schema.json` and one sample `expected-fields.json`; assert the sample also validates against the engine's fragment schema. Fails loudly if the two schemas diverge. **[Agent: python-backend]**
  - [ ] Pytest test for the coverage assertions: deliberately exclude a category from a tempdir-cloned corpus, run the validator, assert it fails with a category-specific message. **[Agent: python-backend]**
  - [ ] **Verify:** `just test-corpus` passes against the real corpus. Manually delete one document-type subtree in a scratch copy and confirm the validator fails with a clear "missing document type" message. Run `just test` end-to-end — all green. **[Agent: python-backend]**
