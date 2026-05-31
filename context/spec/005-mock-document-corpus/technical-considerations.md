# Technical Specification: Mock-Document Corpus

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Draft
- **Author:** Dusty

---

## 1. High-Level Technical Approach

Extend the existing `corpus/` tree with a sibling `corpus/pdf/` subtree dedicated to **PDF extraction quality**: a deterministic-data / randomized-noise PDF generator, committed Layer 1 scenarios + their per-PDF expected-fields, a gitignored Layer 2 slot for real PDFs + per-PDF expected-fields, one JSON schema (`expected-fields`), a standalone CLI runner that invokes the extractor and reports PASS / FAIL + diff + accuracy %, and a fast validator wired into `just test`.

The corpus is a **standalone artifact**. It defines one stable interface — `extractor(pdf_path) -> ExtractedFields` — which a later spec (AI Document Understanding) implements. Until that module exists, the runner reports `extractor not wired` gracefully; the corpus itself (PDFs, JSON, schema, generator, runner skeleton) is independently shippable.

Routes are intentionally out of scope here. The fragment corpus at `corpus/scenarios/` already proves route assembly from extracted fragments. Together: green PDF corpus + green fragment corpus = end-to-end confidence, without this spec needing to build or invoke an engine.

We reuse existing patterns: deterministic generator + `jsonschema` validation + a `just`-recipe entry point + a "drift-check via tempdir regen" CI gate. The twist for PDFs: the **JSON** is drift-checked (data is deterministic); the **PDF bytes** are *not* drift-checked (noise is intentionally variable so regeneration produces new layouts on demand).

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Directory Layout

```
corpus/pdf/
├─ README.md                        # what this is, how to add scenarios, how to run
├─ generator/
│  ├─ __main__.py                   # `python -m corpus.pdf.generator`
│  ├─ matrix.py                     # scenario matrix: doc types × shapes × edge cases × rendering
│  ├─ scenario.py                   # one scenario: stable data + per-scenario noise seed
│  ├─ data.py                       # deterministic data generation (cities, dates, names, prices, QR payloads)
│  ├─ noise.py                      # randomized noise (ads, T&Cs, layout variations, marketing blocks)
│  ├─ render.py                     # WeasyPrint orchestration + PyMuPDF rasterization
│  └─ templates/
│     ├─ air-ticket.html.j2
│     ├─ rail-ticket.html.j2
│     ├─ bus-ticket.html.j2
│     ├─ hotel-booking.html.j2
│     ├─ airbnb-booking.html.j2
│     ├─ supplementary.html.j2
│     ├─ partials/                   # noise blocks (ads, footers, T&Cs) included randomly
│     └─ styles/                     # CSS palettes per fictional brand
├─ schema/
│  └─ expected-fields.schema.json   # the only ground-truth shape
├─ layer1/scenarios/
│  └─ NNN-<slug>/
│     ├─ document.pdf                # committed look-alike PDF (one scenario = one PDF)
│     ├─ expected-fields.json        # per-PDF ground truth; drift-checked
│     └─ README.md                   # one-line scenario summary
├─ layer2/                           # gitignored
│  └─ <trip-slug>/                   # trips group real PDFs for organizational clarity only;
│     ├─ 01-<doc>.pdf                # ground truth is still per-PDF
│     ├─ 01-<doc>.expected-fields.json
│     ├─ 02-<doc>.pdf
│     ├─ 02-<doc>.expected-fields.json
│     └─ README.md
├─ runner.py                        # `python -m corpus.pdf.runner`
└─ validate.py                      # schema check + JSON drift check + PDF/JSON consistency sanity
```

Gitignore addition: `corpus/pdf/layer2/**` with a placeholder `corpus/pdf/layer2/.gitkeep` so the directory exists.

### 2.2 Schema

| Schema | Path | Top-level fields |
|---|---|---|
| `expected-fields` | `corpus/pdf/schema/expected-fields.schema.json` | `document_type` (enum: `air_ticket` / `rail_ticket` / `bus_ticket` / `hotel_booking` / `airbnb_booking` / `supplementary`), `cities[]` (printed names; ≥1), `stations[]` (transit endpoints, each with optional `departure_datetime` / `arrival_datetime`), `accommodations[]` (lodging, each with required `check_in_datetime` + `check_out_datetime`), `venues[]` (sightseeing / parking / other, each with optional `valid_from_datetime` / `valid_to_datetime`), `travelers[]`, `prices[]` (`amount` + `currency`), `qr_codes[]` (raw payload string), `pdf_kind` (`text` \| `rasterized`), `scenario_id`, `noise_seed` (Layer 1 only) |

`stations[]`, `accommodations[]`, `venues[]` all share a common `{ city, kind, identifier }` core but partition by concern AND carry bucket-specific datetime fields. All datetime values are ISO 8601 local datetimes (`YYYY-MM-DDTHH:MM:SS`, no timezone — that mirrors what PDFs actually print).

- **stations** — `kind ∈ {airport, rail_station, bus_terminal}`; `identifier` is the IATA code for airports, the printed station name for rail/bus. Optional `departure_datetime` (when you leave this station) and `arrival_datetime` (when you arrive at this station). An origin has departure only; a terminus has arrival only; a layover or return-leg turnaround has both. A single station may appear multiple times in `stations[]` if it plays different roles on the same ticket (e.g., CDG on a return ticket).
- **accommodations** — `kind ∈ {hotel, airbnb}`; `identifier` is the property name. Required `check_in_datetime` and `check_out_datetime`.
- **venues** — `kind ∈ {sightseeing, parking, other}`; `identifier` is the venue name. Optional `valid_from_datetime` and `valid_to_datetime`.

There is no top-level `dates[]` or `times[]` — every meaningful temporal value lives on the place it belongs to. This keeps arrival/departure pairings intact and unambiguously bound to specific stations.

Per-`document_type` minimum counts (enforced by the validator, not the schema):
- `air_ticket` / `rail_ticket` / `bus_ticket`: `stations[]` ≥ 2 (departure + arrival); for every transit-ticket station entry, at least one of `departure_datetime` / `arrival_datetime` must be set.
- `hotel_booking` / `airbnb_booking`: `accommodations[]` ≥ 1.
- `supplementary`: no minimum on `venues[]` (city-only supplementary is legal).

Every entry's `city` must match a value in `cities[]` (validator-enforced integrity rule, not in the schema).

`pdf_kind` tells the validator which sanity check to apply, and gives the runner a baseline to compare against the extractor's `extraction_path` (see §2.5, §2.6).

**Note on engine fragment schema alignment:** The engine's existing fragment schema (`corpus/schema/extracted-fragment.schema.json`) currently has only `cityCode`. Aligning it with this richer extracted-fields shape (printed `cities[]` + structured `stations[]` / `accommodations[]` / `venues[]`) is tracked as a follow-up ticket on the route-engine parent. Until that lands, the cross-schema sanity check in §2.7 step 4 is reduced to a documented-mapping note rather than direct validation.

Draft 2020-12. Validated with the existing `jsonschema` dependency.

### 2.3 Generator (Layer 1)

- **Stable data** (`generator/data.py`): scenario-axis-derived city pool (deterministic SHA-256 seed), dates anchored at the existing fixed epoch (`2027-03-01T00:00:00Z`), traveler names from a stable pool, prices/currencies, QR payloads (fake but realistic shape).
- **Randomized noise** (`generator/noise.py`): marketing-banner inclusion (0–2), T&C block presence/position, footer ad variant, font-pair pick (small palette), partial inclusion order, secondary QR placement.
- **Determinism contract:**
  - *Data layer:* given a scenario spec, the same data values come out every time. **Drift-checked.**
  - *Noise layer:* uses `Random(noise_seed)`. `noise_seed` defaults to fresh entropy on regeneration but is recorded in `expected-fields.json` so any committed PDF is reproducible via `--noise-seed`.
- **Scenario coverage** (per the functional spec's required shapes):

| Axis | Values |
|---|---|
| Document type | air, rail, bus, hotel, airbnb, supplementary |
| Trip shape (where multiple PDFs share a `trip_id`) | single-leg, 2-leg, 3+leg (≥3 scenarios), return-ticket (≥3) |
| Travelers | 1, 2+ (≥3 multi-traveler scenarios) |
| Supplementary | ≥3 scenarios are standalone supplementary docs |
| Rendering | `text` (default; WeasyPrint PDF with text layer) \| `rasterized` (~15% of scenarios; same template + data, each page converted to a ~120 DPI image with PyMuPDF and re-embedded as an image-only PDF) |
| Language | English only (v1) |

Matrix enumerated in `generator/matrix.py`; scenario IDs are `NNN-<slug>` and stable across regenerations. **Each Layer 1 scenario is a single-PDF unit** (one `document.pdf` + one `expected-fields.json` per scenario directory). Layer 1 ships ~150 individual PDFs total — i.e. ~150 single-PDF scenario directories — with ~22 of those rendered as `rasterized` to deterministically exercise the vision-fallback extraction path. Same data, same `expected-fields.json` either way — only `pdf_kind` differs. The multi-PDF "trip" framing only applies to Layer 2 (contributor-supplied real PDFs), where grouping is purely organizational.

- **Rendering:** Jinja2 + WeasyPrint for the text path. For the rasterized path, `render.py` takes the WeasyPrint output and uses PyMuPDF to render each page to a PNG at ~120 DPI ("web quality"), then re-emits as an image-only PDF. Fonts ship with templates and are open-source (Inter, IBM Plex) so the toolchain is portable.

### 2.4 Layer 2 (Real PDFs)

No generator. The team drops real PDFs into `corpus/pdf/layer2/<trip-slug>/` and authors a sibling `*.expected-fields.json` per PDF — including `pdf_kind`. Determining `pdf_kind` is trivial: open the PDF in any viewer; if text selects, it's `text`, otherwise `rasterized`.

Trip grouping is purely organizational (helps a contributor remember "these 5 PDFs came from my Porto trip"); every assertion is still per-PDF.

The runner discovers PDFs by walking the directory; each `*.expected-fields.json` is validated against the schema.

### 2.5 Runner CLI

Entry point: `python -m corpus.pdf.runner [--layer 1|2|both] [--filter <pattern>] [--json-report <path>]`. Wrapped by `just test-pdf-corpus`.

Per-PDF flow (identical for both layers):

1. Read PDF + sibling `expected-fields.json`.
2. Call `extractor(pdf_path) -> ExtractedFields`.
3. Compare extracted fields against expected, field by field per the schema.
4. PASS = every field matches; FAIL otherwise.

Report shape:

```
Layer 1 (synthetic): 143/150 PASS  (95.3%)   path: text=127  vision=23
Layer 2 (real):       21/24  PASS  (87.5%)   path: text=18   vision=6
TOTAL:               164/174 PASS  (94.3%)

FAILED:
  L1 023-cdg-via-fra/document.pdf
    cities:   expected ["Paris","Frankfurt"]   actual ["Paris"]
    prices:   expected [{"amount":89.50,"currency":"EUR"}]
              actual   [{"amount":89.5,"currency":"EUR"}]   ← numeric-format mismatch
  L2 porto-trip/02-hotel-booking.pdf
    dates:    expected ["2026-10-12","2026-10-15"]   actual ["2026-10-12"]

DIAGNOSTIC (non-failing):
  L1 037-doc.pdf  pdf_kind=text but extractor fell back to vision  ← perf regression worth a look
```

The path-mix summary (extracted from each result's `extraction_path`) makes accidental fallback regressions visible before they bite the bill. The "fell back when text was available" line is informational, not PASS/FAIL.

Optional `--json-report` writes a machine-readable equivalent for future CI integration.

### 2.6 Extractor Interface

The runner depends on **one** callable — defined here, implemented in the AI Document Understanding spec:

| Interface | Module (proposed) | Signature |
|---|---|---|
| Extractor | `where_tickets.extraction.extract_pdf` | `(pdf_path: Path) -> ExtractedFields` where `ExtractedFields` matches `expected-fields.schema.json` plus an optional `extraction_path: Literal["text", "vision"]` recording which internal path was used. |

The runner uses `extraction_path` for the path-mix summary and the diagnostic cross-check in §2.5. The two-stage pipeline (PyMuPDF text → Haiku, with PyMuPDF rasterize → Sonnet vision → Haiku as fallback) lives entirely behind this interface in the AI Document Understanding spec.

Until the module exists, the runner imports it lazily and reports `extractor not wired: install per AI Document Understanding spec` instead of crashing. The corpus + runner remain shippable on their own.

### 2.7 Validator

`corpus/pdf/validate.py` runs in CI via `just test-corpus` (extended) and does:

1. JSON Schema validation of every committed `expected-fields.json`.
2. **JSON drift check:** regenerate the Layer 1 scenarios' JSON into a tempdir using the data layer (no rendering), and diff against committed JSON. Any drift fails.
3. **PDF/JSON consistency sanity**, split by `pdf_kind`:

| `pdf_kind` | Sanity check |
|---|---|
| `text` | Assert PyMuPDF extracts a non-empty text layer AND every `cities[]` token + every distinct date portion (`YYYY-MM-DD`) of any `*_datetime` field across `stations[]` / `accommodations[]` / `venues[]` appears verbatim in that text. |
| `rasterized` | Assert PyMuPDF extracts an empty (or whitespace-only) text layer — confirms the PDF is genuinely image-only. Token-presence check is skipped (there's no text to find). |

4. **Cross-schema mapping (documented; direct validation deferred):** Until the engine fragment schema is extended to match the richer extracted-fields shape (tracked in the route-engine follow-up ticket), this check ships as a documented mapping note in `corpus/pdf/README.md` rather than a runtime assertion: `cities[]` → fragment `cityCode` via lookup; `stations[]` / `accommodations[]` / `venues[]` are extra context the engine currently ignores. Once the fragment-schema follow-up lands, this step upgrades to a real `jsonschema.validate(sample, fragment_schema)` test.
5. **Layer 2 leak guard:** `git ls-files corpus/pdf/layer2/` must be empty (one-line guard catches accidental `git add`).
6. Layer 2 is opt-in: skipped if the directory is empty.

### 2.8 Just Recipes

| Recipe | Action |
|---|---|
| `just regen-pdf-corpus` | Regenerate Layer 1 PDFs + JSON. `python -m corpus.pdf.generator`. |
| `just test-pdf-corpus` | Run the runner (Layer 1 + Layer 2). Reports degraded gracefully if extractor missing. |
| `just test-corpus` (extended) | Existing fragment validator **+** new `corpus/pdf/validate.py`. Wired into `just test`. |

### 2.9 Dependencies

Added to the backend `corpus` dep group (or a new `pdf-corpus` group — to be confirmed at task time):

| Package | Purpose | Notes |
|---|---|---|
| `weasyprint` | HTML/CSS → PDF rendering | Native deps: Pango / Cairo. macOS: `brew install pango`. CI image must include these. |
| `jinja2` | Template engine | Pure Python. |
| `pymupdf` | Rasterization in the generator + text extraction in the validator | Already in backend deps. |
| `jsonschema` | Schema validation | Already in use by the fragment corpus validator. |

No new runtime deps for the application — the corpus tree is dev/test only.

---

## 3. Impact and Risk Analysis

### System Dependencies

- The runner's PASS / FAIL behavior depends on `where_tickets.extraction.extract_pdf` landing in the AI Document Understanding spec. The runner is built defensively so the corpus itself is independently shippable.
- The validator depends on `pymupdf` (already a backend dep) and `jsonschema` (already in use).
- WeasyPrint introduces native system dependencies (Pango, Cairo). Affects local-dev setup and the CI image.
- The `expected-fields` schema is implicitly coupled to the engine's accepted fragment schema (`corpus/schema/extracted-fragment.schema.json`). If the engine schema evolves, this one must follow.

### Potential Risks & Mitigations

| Risk | Mitigation |
|---|---|
| WeasyPrint native deps make local setup painful. | Document install (`brew install pango` on macOS) in `corpus/pdf/README.md`; verify the CI image has them. Generator has a `--check-deps` flag with a clear error. |
| Randomized noise drifts toward unrealistic layouts over time (100 ad blocks). | Bound every random choice in `noise.py` with a small palette (counts capped, fonts from a fixed pool, partials from a fixed catalog). Smoke unit test on `noise.py` asserts outputs stay within bounds. |
| Hand-editing one side (PDF template or JSON) without regenerating leaves them out of sync. | The validator's PDF/JSON consistency check (§2.7 step 3) catches this on CI. |
| Rasterization DPI choice (too low → vision can't read it; too high → bloated PDFs and slow runs). | Pin at ~120 DPI ("web quality" per the extraction tactics). Document the knob in `corpus/pdf/README.md`. |
| Layer 2 contributors mis-tag `pdf_kind`. | Validator's `rasterized` check asserts text layer is empty; surfaces a clear error if a `text` PDF was mis-tagged as `rasterized` or vice versa. |
| Fictional brand logos resemble a real airline / hotel chain. | Use generic geometric marks; ban real brand names / colors. Documented in `corpus/pdf/README.md`. |
| Real PDFs leak into the repo via accidental `git add`. | Gitignore covers all of `corpus/pdf/layer2/`. `validate.py` step §2.7.5 guards on CI. |
| Coverage target (~50 trips / ~150 PDFs) is unrealistic to author template-by-template. | The matrix-driven generator scales authoring effort with template count (6), not scenario count (~150). |
| Extractor schema and engine fragment schema diverge silently. | Validator step §2.7.4 cross-checks them on every CI run. |

---

## 4. Testing Strategy

| Layer | What's tested | How |
|---|---|---|
| Schemas | `expected-fields.schema.json` is valid Draft 2020-12 | Unit test loads it with `jsonschema.Draft202012Validator.check_schema`. |
| Schema compatibility | `expected-fields` is engine-fragment-compatible | Unit test validates a sample payload against both schemas. |
| Data generation | Deterministic across runs | `validate.py`'s JSON drift check. Wired into `just test`. |
| PDF/JSON consistency | Every committed `text` PDF contains its data tokens; every `rasterized` PDF has an empty text layer | `validate.py`'s split-by-`pdf_kind` sanity check. Wired into `just test`. |
| Noise bounds | Noise stays within configured palettes | Pytest unit test on `noise.py` with a seeded RNG. |
| Runner | Reports correctly on a tiny synthetic fixture | Pytest test runs the runner against a 2-scenario fixture (one `text`, one `rasterized`) with a stub extractor; asserts pass/fail counts, path-mix summary, and report shape. |
| Extraction quality | Real extractor against the full corpus | `just test-pdf-corpus`. Becomes a meaningful gate once AI Document Understanding lands; informational until then. |
