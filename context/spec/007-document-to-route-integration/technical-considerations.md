# Technical Specification: Document-to-Route Integration

- **Functional Specification:** `context/spec/007-document-to-route-integration/functional-spec.md`
- **Linear:** [DUS-31](https://linear.app/dusty-work/issue/DUS-31/extend-engine-fragment-schema-for-cities-stations-accommodations)
- **Status:** Draft
- **Author(s):** Alexander Shleyko

---

## 1. High-Level Technical Approach

Three changes land together, in the same work item:

1. **Engine fragment shape grows** to match the extractor's output. The current `Fragment` union (transit ticket / hotel booking, IATA `cityCode`, `legs[]`) is replaced with a richer shape carrying `cities[]`, `stations[]`, `accommodations[]`, `venues[]`, plus two new document kinds (`airbnb-booking`, `supplementary`). The `cityCode` IATA pattern is removed: the engine identifies cities by their printed name, and airport / rail / bus identifiers move onto the new `Station` entries. `RouteStop.city` and `expected-route.json` get the same treatment.
2. **The committed corpora are regenerated** into the new shape: the existing 192-scenario fragment corpus (`corpus/scenarios/`) and the existing `corpus/schema/extracted-fragment.schema.json` + `corpus/schema/expected-route.schema.json`. The algorithmic engine continues to score 192/192, and the engine's existing offline tests + determinism gate continue to pass after the regeneration.
3. **A new integration runner** lands at `backend/spikes/integration/`. It discovers ~20–30 multi-PDF trips from a new `corpus/integration/` tree, calls the live production extractor for each PDF, feeds the results through a thin adapter into the algorithmic engine (per trip, fragments replayed in upload order), and asserts the final `WorkingRoute` against the trip's committed `expected-route.json`. A 100% hard gate. Invoked via `just integration`.

The engine stays inside `backend/spikes/route_engine_algorithmic/` and continues to import shared types from `spikes.route_engine_llm.*` — promotion out of `spikes/` and the SQS wiring listed in the ADR's "Consequences" remain explicitly out of scope.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Module layout

| Path | Responsibility |
|------|---------------|
| `spikes/route_engine_llm/models.py` | Reshape `Fragment` and the working models per §2.2 (the LLM spike's models are the canonical engine types until promotion). |
| `spikes/route_engine_llm/operations.py` | Add a `attach_venue` op and a `add_unattached_document` op; extend `RouteStop` with `stations[]` + `venues[]`; extend `WorkingRoute` with `unattached_documents[]`. Stop projection logic adapts to new fields. |
| `spikes/route_engine_algorithmic/rules.py` | Extend `build_ops` and `classify_event` to (a) derive arrival/departure events from the fragment's `stations[]` rather than its old `legs[]`, (b) emit `attach_venue` ops for venues with a city, (c) emit `add_unattached_document` ops for supplementary documents with no routable place. |
| `corpus/schema/extracted-fragment.schema.json` | Updated to the new shape (see §2.2); legacy `cityCode` / `legs[]` removed. |
| `corpus/schema/expected-route.schema.json` | `cityCode` pattern removed (free-form city name); `stops[].venues`, `stops[].stations`, top-level `unattachedDocuments` added. |
| `corpus/generator/` | Regenerate 192 scenarios into the new shape; the generator already knows the underlying trip data, so this is reshaping its output, not changing what it generates. |
| `corpus/integration/` (new) | One directory per integration trip. Each contains `manifest.json` (ordered list of source-PDF paths from `corpus/pdf/layer1/`, plus traveler list and any `expect_unreadable: true` flags) and `expected-route.json`. README documents the coverage matrix. |
| `backend/spikes/integration/__init__.py` | Package marker. |
| `backend/spikes/integration/adapter.py` | `extracted_fields_to_fragment(fields: ExtractedFields, *, source_document_id: str) -> Fragment`. Pure mapping: snake_case → engine fragment shape, station-datetime pairing into arrival/departure events, no I/O. |
| `backend/spikes/integration/runner.py` | CLI entrypoint. Discovers trips from `corpus/integration/`, runs `extract_pdf` live for each manifest PDF, adapts to fragments, replays through `engine.update_route` in manifest order, asserts via the same `scoring.final_route_match` used by the engine corpus runner, prints PASS/FAIL summary + per-trip failures, writes a JSON report under `runs/`. |
| `backend/spikes/integration/report.py` | Renders the per-run summary and (per-failure) the expected-vs-actual diff. Reuses `spikes.route_engine_llm.report` helpers where applicable. |
| `backend/tests/spikes/test_adapter.py` | Unit tests for `extracted_fields_to_fragment` across all six document kinds, single-leg and return, multi-traveler, missing datetimes, venue with and without location. |
| `backend/tests/spikes/test_rules_new_shape.py` | Unit tests covering the new rule branches (venue-with-city event, venue-no-location event, station-pair derivation, airbnb booking event). |
| `backend/tests/spikes/test_integration_runner.py` | Runner-level tests with a stub extractor (no live Bedrock), exercising the "PDF unreadable → trip still builds" path and the discovery/comparison logic. |
| `justfile` | New `integration` recipe (see §2.6). |

### 2.2 Engine fragment shape (the format bridge)

`Fragment` becomes a single object (rather than a discriminated union of dissimilar shapes), with `documentType` driving validation rules:

| Field | Type | Notes |
|---|---|---|
| `documentType` | enum: `air-ticket`, `rail-ticket`, `bus-ticket`, `hotel-booking`, `airbnb-booking`, `supplementary` | Aligned with the extractor's six kinds. The legacy `train-ticket` is renamed to `rail-ticket` to match the extractor. |
| `sourceDocumentId` | str | Stable handle for traceability and the engine's `Transit.sourceFragmentId`. |
| `travelers` | list[str] | Unchanged. |
| `cities` | list[str] | Printed city names exactly as they appear on the document. |
| `stations` | list[Station] | Each: `city`, `kind` (`airport` / `rail_station` / `bus_terminal`), `identifier`, optional `departureAt`, optional `arrivalAt`. Empty for non-transit documents. |
| `accommodations` | list[Accommodation] | Each: `city`, `kind` (`hotel` / `airbnb`), `identifier`, `checkInAt`, `checkOutAt`. Empty for non-lodging documents. |
| `venues` | list[Venue] | Each: `city`, `kind` (`sightseeing` / `parking` / `other`), `identifier`, optional `validFromAt`, optional `validToAt`. |
| `prices` | list[Price] | Carried through unchanged for downstream display; not consumed by routing. |
| `qrCodes` | list[str] | Carried through unchanged; not consumed by routing. |
| `pdfKind` | enum: `text` / `rasterized` | Carried through; useful diagnostic, not routed on. |
| `pnr` / `confirmationCode` | str \| None | Optional, kept for traceability where present. |

The 3-letter IATA pattern on `cityCode` is removed everywhere it appeared (`Fragment.legs[].from/to`, `RouteStop.city`, `ExpectedStop.city`, `ExpectedTransit.from/to`). `Leg` disappears from `Fragment` entirely — the engine derives ordered arrival/departure events from `stations[]` (see §2.4).

### 2.3 Working-route shape

| Model | Change |
|---|---|
| `RouteStop` | `city: str` (no pattern). Add `stations: list[Station]` (the stations in this city contributed by transits/PDFs) and `venues: list[VenueRef]` (venues attached to this city). `accommodations[]` keeps its current shape; `Accommodation` gains `kind` (`hotel` / `airbnb`) and `identifier` (printed name). |
| `Transit` | Add `originStation: Station \| None` and `destinationStation: Station \| None` (which station within the from/to city the transit actually departs/arrives at). `mode` enum gains parity with documentType (`air` / `bus` / `rail`); existing `train` is renamed `rail`. |
| `WorkingRoute` | Add `unattached_documents: list[UnattachedDocument]` for supplementary documents with no routable place. Each entry: `sourceDocumentId`, `documentType`, `pricesRef`, `qrCodesRef` — kept on the route so downstream UI surfaces them. |

City identity (used by `classify_event` to find the same-city stop) is the printed city name after a simple normalizer (`.strip().casefold()`). Same-city-different-station collapse comes for free: two stations with the same normalized city land on one `RouteStop`, each entering the `stations[]` list.

### 2.4 Algorithmic rules pipeline updates

`rules.build_ops(route, fragment)` keeps its overall shape — identify events → classify CREATE-vs-ENRICH → emit ops — with three concrete additions:

1. **Station pairing.** For transit-ticket fragments (`air-ticket` / `rail-ticket` / `bus-ticket`), the rules derive ordered legs from `stations[]` by sorting station entries chronologically and pairing `(stationA with departureAt → stationB with arrivalAt)`. Stations with both timestamps (rare; layover or return-leg turnaround) yield two events — an arrival and a departure — at the same `RouteStop`. A station with only `arrivalAt` or only `departureAt` contributes one event. The legacy `Leg` concept is moved out of the public `Fragment`; an internal `_LegView` dataclass keeps the existing classifier loop unchanged.
2. **Accommodation events** now carry a `kind`. The classifier treats `hotel` and `airbnb` identically when filling the "accommodation" slot; the kind is preserved on the resulting `Accommodation` entry attached to the stop.
3. **Venue events.** A venue with a city contributes one event analogous to an accommodation (single time anchor = `validFromAt` if present, else `checkInAt`-equivalent fallback — when neither is present the venue is attached at projection time without a chronological anchor). The rules emit a new `attach_venue` op that the applier appends to the target stop's `venues[]`. The pending-projection ledger gains a `venues` bucket so condition (c) sees in-batch venue additions.
4. **Supplementary without a place.** When a `supplementary` fragment has no entries in `stations[]`, `accommodations[]`, or `venues[]`, the rules emit a single `add_unattached_document` op; the applier appends an `UnattachedDocument` to `WorkingRoute.unattached_documents` and stops/transits are untouched.

The applier's identity-preservation, gap-safe insertion, and stop projection from transits all continue to apply unchanged; the new ops slot in next to the existing ones.

### 2.5 Integration corpus layout

`corpus/integration/<slug>/` contains, per trip:

- `manifest.json`:
  - `travelers: list[str]` — the trip's travelers (sanity-checked against the extracted fragments).
  - `documents: list[{ pdf: str, expect_unreadable?: bool }]` — ordered list of paths to source PDFs (relative to `corpus/pdf/layer1/`); the PDFs are NOT duplicated, just referenced. `expect_unreadable: true` flags a PDF the runner should accept as a failed extraction without failing the trip (exercises §2.6 of the functional spec).
  - `notes: str` (optional) — human-readable description of what the trip covers, used in the runner's failure output.
- `expected-route.json`: same shape as the engine corpus's `expected-route.json` (updated per §2.2), describing the route the engine should produce from the readable PDFs in `documents`.
- `README.md` at the trip level is optional; one `corpus/integration/README.md` at the top of the tree documents the coverage matrix mapping each trip slug to which dimension(s) it covers.

Trip selection is driven by the coverage matrix in the functional spec §2.7. A representative starting cut (subject to refinement during implementation):

| # | Slug | Covers |
|---|---|---|
| 1–4 | `straight-3city-1pax-{air,rail,bus,mixed}` | Per-mode straight-line, single traveler, three cities. |
| 5–8 | `return-{air,rail,bus,mixed}-1pax` | Return journeys. |
| 9–10 | `straight-2pax-air-with-hotels` | Multi-traveler + accommodation interleaving. |
| 11 | `airbnb-leg-2pax-rail` | Airbnb stay. |
| 12 | `same-city-two-airports` | Paris CDG / ORY collapse. |
| 13 | `divergent-travelers-mid-trip` | One traveler diverges. |
| 14–15 | `supplementary-{voucher,parking}` | Supplementary docs (one with city, one without). |
| 16 | `sightseeing-venue-on-stop` | Venue with city. |
| 17–18 | `scan-pdf-mixed-trip-{air,rail}` | At least one scan-style PDF per trip. |
| 19 | `unreadable-pdf-in-trip` | Exercises §2.6. |
| 20–25 | Five further single-purpose trips per discovered failure mode during iteration. |
| 26–30 | Five "real-shape" trips chosen to mirror common European itineraries the team has seen in practice. |

### 2.6 Runner CLI and `justfile`

`just integration` runs the full set live. Per-trip behaviour:

1. Read `manifest.json` and `expected-route.json`.
2. For each entry in `documents`, call `extract_pdf(pdf_path)`. If `ExtractionFailedError` is raised, mark the document as unreadable; the trip is failed unless `expect_unreadable: true` is set on that entry. Otherwise feed the result through `adapter.extracted_fields_to_fragment` to produce a `Fragment`.
3. Replay the fragments through `engine.update_route` in manifest order, starting from an empty `WorkingRoute`.
4. Run `scoring.final_route_match(working_route, expected_route)` (reused from the engine corpus runner) and bucket the trip PASS / FAIL.

CLI flags:

- `--trip <slug>` — run a single trip for fast iteration on a failure.
- `--no-route-check` — extract + adapt only, skip the route assertion (useful for debugging an adapter regression).
- `--json-report <path>` — write a machine-readable summary including per-trip pass/fail, per-PDF extraction path, and (on failure) the diff.

`justfile` recipe (sketch):

```
integration *args:
    cd backend && PYTHONPATH=. uv run --isolated --group extraction --group corpus \
        python -m spikes.integration.runner {{args}}
```

The `--isolated --group extraction` pattern mirrors `just extract-pdf` / `just test-pdf-corpus` so the `anthropic` install stays out of the persistent backend venv (per the existing memory: extraction must stay in an isolated venv).

### 2.7 Migration steps

1. Reshape `corpus/schema/extracted-fragment.schema.json` and `corpus/schema/expected-route.schema.json`. `just test-corpus` will fail until the corpus is regenerated.
2. Extend the generator to emit the new shape; `just regen-corpus` rebuilds `corpus/scenarios/`. Commit the regenerated 192.
3. Update the engine models / operations / rules to consume the new shape. Run `just spike-engine-algo` — should still be 192/192.
4. Add the integration adapter + runner + `corpus/integration/` trips + the `just integration` recipe.
5. Update DUS-30 Slice 7 sub-task 2 to perform real cross-schema validation against the updated fragment schema (as DUS-31 calls for).

---

## 3. Impact and Risk Analysis

- **System dependencies.**
  - Production: none yet — the engine still lives in `spikes/`, there are no Aurora tables to migrate, and the SQS pipeline is not wired. The work touches only the spike, the corpora, and the new integration runner.
  - The production extractor (`where_tickets.extraction.extract_pdf`) is consumed live by the integration runner. The runner imports it via the same isolated-venv pattern as `just extract-pdf` / `just test-pdf-corpus`, so the persistent backend venv (and `just lint`) stays clean.
  - The LLM spike under `backend/spikes/route_engine_llm/` is touched (shared models + operations) but its `bedrock_client.py`, `prompts.py`, `pricing.py`, `report.py`, etc. are untouched. The LLM spike's 101 offline tests should continue to pass with the renamed `train-ticket` → `rail-ticket` and the relaxed city pattern.

- **Potential risks & mitigations.**
  - **Regenerating the 192 fragments destabilises the engine.** Mitigation: do the regeneration BEFORE the engine code changes (so a clean baseline diff is observable: 192/192 with old schema, then schema change, then code change, with the engine corpus check on every step). The engine's determinism test (byte-identical re-runs) continues to apply post-migration.
  - **City identity collisions from name normalization.** "Paris" vs "PARIS" vs "Paris, FR" could end up as separate stops or get incorrectly collapsed. Mitigation: the normalizer is `strip().casefold()` only; the corpus uses the same printed city names consistently. Anything more aggressive (locale folding, accent-stripping) is deferred. If a real PDF produces a variant ("São Paulo" vs "Sao Paulo"), the integration runner surfaces the failure as a city-identity mismatch with both forms visible; the fix is in extraction or a wider normalizer, not in the engine's identity logic.
  - **Station-pairing ambiguity on layovers and returns.** A return air ticket has 4 stations in `stations[]` and they must pair correctly. Mitigation: pair chronologically by sorted `departureAt` / `arrivalAt`; assert per-leg that arrival > departure as a sanity check; failures surface as `RuleNotImplementedError` with the offending station list logged, rather than producing a wrong route silently.
  - **Live-Bedrock variance fails a passing trip on a re-run.** The extractor is at 99.3% on the corpus, so ~1-in-150 risk per PDF; for a trip with 4 PDFs that's a ~2.6% trip-level flake risk per run. Mitigation: trips selected for the integration set are extracted in a quick smoke pass during implementation; any trip whose constituent PDFs are not in the 149 passing scenarios is replaced. The on-demand command logs which PDF failed extraction so a flake is easy to triage and re-run.
  - **Live-Bedrock cost.** ~3-5 PDFs per trip × 20–30 trips × ~$0.01–0.03 per PDF = ~$0.60–4.50 per full run. Mitigation: the runner supports `--trip <slug>` for single-trip reruns; full runs are on-demand, not on every commit; the per-trip cost is logged in the JSON report.
  - **`UnattachedDocument` on the working route bleeds into the engine's identity/ordering checks.** Mitigation: keep `unattached_documents[]` strictly out of `final_route_match`, `identity_preserved`, and `ordering_consistent`. Add one unit test asserting that an unattached-document op never mutates `stops[]`, `transits[]`, or any id counter.
  - **Schema-rename churn (`train` → `rail`, `cityCode` → `city`) hits readers we don't own yet.** Mitigation: there are no readers outside the spike + corpus today (no Aurora tables, no mobile sync, no SQS messages). The rename is a one-step migration with the corpus regeneration. Document the rename in the spec's "Out-of-Scope" note for downstream consumers when they arrive.

---

## 4. Testing Strategy

- **Unit (offline, CI-safe).**
  - `test_adapter.py` — one test per document kind (air, rail, bus, hotel, airbnb, supplementary), plus return tickets, multi-traveler, missing optional datetimes, venue-with-city and venue-without-city, supplementary-with-city and supplementary-without-city. Asserts the resulting `Fragment` is valid per the new schema.
  - `test_rules_new_shape.py` — explicit coverage of (i) station-pair derivation across 1 / 2 / 3 / 4 stations, (ii) the new `attach_venue` op, (iii) the new `add_unattached_document` op, (iv) same-city-different-station collapse, (v) airbnb routed identically to hotel.
  - `test_integration_runner.py` — runner-level tests with a stub `extract_pdf` that returns canned `ExtractedFields`, exercising discovery, the unreadable-document path, the `--trip` flag, and the JSON report shape. No live Bedrock.

- **Engine corpus regression.**
  - `just test-corpus` (schema-validate fragments + expected-routes + jsonschema cross-check per DUS-31 done-when).
  - `just spike-engine-algo` over all 192 — gate: 192/192. The existing offline tests under `backend/tests/spikes/` should pass unchanged after the shape migration.
  - Determinism — two back-to-back `just spike-engine-algo` runs produce byte-identical `results.json` (modulo timestamp and per-scenario latency), same as today.

- **Integration end-to-end (live Bedrock; on-demand).**
  - `just integration` over the 20–30 trips — gate: 100% pass. Failures surface per-trip with the extractor outputs, the produced route, the expected route, and a per-field diff.
  - `just integration --trip <slug>` for single-trip debugging.
  - One scenario explicitly flagged `expect_unreadable: true` covers §2.6.

- **What is NOT tested.**
  - Performance / cost gates — no hard target; the JSON report includes timings and per-PDF token counts for observation only.
  - The promoted-engine production location, SQS wiring, Aurora persistence — all explicitly out of scope per the ADR.
