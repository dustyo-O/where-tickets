# Tasks: Document-to-Route Integration

> Each slice is end-to-end runnable. Don't move on until the slice's verification passes.

---

- [ ] **Slice 1: Rename `train` → `rail` everywhere**
  - [ ] Update `corpus/schema/extracted-fragment.schema.json`: change `transitTicket.documentType` enum from `train-ticket` to `rail-ticket`. **[Agent: python-backend]**
  - [ ] Update `corpus/schema/expected-route.schema.json`: change `transit.mode` enum from `train` to `rail`. **[Agent: python-backend]**
  - [ ] Update `corpus/generator/` so regenerated fragments and expected-routes emit `rail-ticket` / `rail`. **[Agent: python-backend]**
  - [ ] Update `spikes/route_engine_llm/models.py`: `TransitMode.TRAIN = "train"` → `RAIL = "rail"`; `TransitTicketFragment.documentType` literal includes `rail-ticket` instead of `train-ticket`. **[Agent: python-backend]**
  - [ ] Update `spikes/route_engine_llm/corpus.py` and `scoring.py` for the rename. **[Agent: python-backend]**
  - [ ] Regenerate the 192-scenario corpus via `just regen-corpus`; commit the result. **[Agent: python-backend]**
  - [ ] **Verify:** `just test-corpus` green; `just spike-engine-algo` reports 192/192; `cd backend && uv run pytest tests/spikes/` green; `just lint` clean. **[Agent: python-backend]**

- [ ] **Slice 2: Drop the IATA pattern; city is a printed name + normalizer collapse**
  - [ ] Remove the 3-letter pattern from `cityCode` in both schemas; rename the JSON Schema `$def` accordingly (`cityCode` → `cityName`); update all `pattern` references. **[Agent: python-backend]**
  - [ ] Update the generator to emit printed city names ("Paris", "Lisbon") on legs / accommodations / expected-route stops instead of IATA codes. **[Agent: python-backend]**
  - [ ] Update Pydantic models in `spikes/route_engine_llm/models.py` and `corpus.py` — drop `Field(pattern=r"^[A-Z]{3}$")` on `RouteStop.city`, `Leg.from_/to`, `ExpectedStop.city`, `ExpectedTransit.from_/to`. **[Agent: python-backend]**
  - [ ] Add a small `city_identity(name: str) -> str` helper (`strip().casefold()`) in `spikes/route_engine_llm/models.py`; route the classifier's same-city lookups through it. **[Agent: python-backend]**
  - [ ] Unit test: `RouteStop` with city `"Paris"` collapses with a same-fragment leg using `"PARIS"`. **[Agent: python-backend]**
  - [ ] Regenerate the 192 corpus; commit. **[Agent: python-backend]**
  - [ ] **Verify:** `just test-corpus` green; `just spike-engine-algo` 192/192; `cd backend && uv run pytest tests/spikes/` green; `just lint` clean. **[Agent: python-backend]**

- [ ] **Slice 3: Replace `legs[]` with `stations[]` on transit fragments**
  - [ ] Reshape `transitTicket` in `corpus/schema/extracted-fragment.schema.json`: remove `legs[]`, add `stations[]` (each `{ city, kind ∈ {airport, rail_station, bus_terminal}, identifier, departureAt?, arrivalAt? }`) and `cities[]`. **[Agent: python-backend]**
  - [ ] Update the generator to emit `stations[]` (paired chronologically) and `cities[]` (city names) instead of `legs[]`. **[Agent: python-backend]**
  - [ ] Add `Station` Pydantic model in `spikes/route_engine_llm/models.py`; replace `Fragment.legs` with `Fragment.stations` + `Fragment.cities`. **[Agent: python-backend]**
  - [ ] Add an internal `_LegView` dataclass in `spikes/route_engine_algorithmic/rules.py`; derive legs from `stations[]` by sorting by `(departureAt or arrivalAt)` and pairing departure → arrival. **[Agent: python-backend]**
  - [ ] Extend `RouteStop` with `stations: list[Station]` and `Transit` with `originStation` / `destinationStation`; populate them in `operations.apply` when `create_stop` / `add_transit` execute. **[Agent: python-backend]**
  - [ ] Unit tests for `_LegView` derivation across 1 / 2 / 3 / 4 station inputs (straight, return, layover, dual-direction). **[Agent: python-backend]**
  - [ ] Regenerate the 192 corpus; commit. **[Agent: python-backend]**
  - [ ] **Verify:** `just test-corpus` green; `just spike-engine-algo` 192/192; offline tests green; `just lint` clean. **[Agent: python-backend]**

- [ ] **Slice 4: Accommodations carry `kind` + `identifier`**
  - [ ] Reshape `hotelBooking` in the fragment schema: remove `city` + `checkInAt` + `checkOutAt` + `hotelName` as top-level; replace with `accommodations[]` (each `{ city, kind ∈ {hotel, airbnb}, identifier, checkInAt, checkOutAt }`) + `cities[]`. **[Agent: python-backend]**
  - [ ] Update the generator to emit `accommodations[]` with `kind: "hotel"` for existing scenarios. **[Agent: python-backend]**
  - [ ] Update `HotelBookingFragment` Pydantic model (rename to `AccommodationFragment`, retain `documentType: "hotel-booking"`); `Accommodation` model gains `kind: Literal["hotel", "airbnb"]` and `identifier: str`. **[Agent: python-backend]**
  - [ ] Update `expected-route.schema.json` `accommodation` to carry `kind` and `identifier`; regenerate expected-routes. **[Agent: python-backend]**
  - [ ] Update `rules.py` event extraction to read from `accommodations[]` instead of single-shot fields. **[Agent: python-backend]**
  - [ ] Regenerate the 192 corpus; commit. **[Agent: python-backend]**
  - [ ] **Verify:** `just test-corpus` green; `just spike-engine-algo` 192/192; offline tests green; `just lint` clean. **[Agent: python-backend]**

- [ ] **Slice 5: Airbnb + supplementary doc types, venues, unattached docs**
  - [ ] Add `airbnb-booking` and `supplementary` to the `documentType` enum in the fragment schema and `AccommodationFragment` / new `SupplementaryFragment` Pydantic types. **[Agent: python-backend]**
  - [ ] Add `Venue` model + `venues: list[Venue]` to `Fragment`; extend the fragment schema to carry it. **[Agent: python-backend]**
  - [ ] Add `RouteStop.venues: list[Venue]` and `WorkingRoute.unattached_documents: list[UnattachedDocument]` in `spikes/route_engine_llm/models.py`. **[Agent: python-backend]**
  - [ ] Add `attach_venue` and `add_unattached_document` ops in `spikes/route_engine_llm/operations.py` (each with its applier branch). Keep `unattached_documents[]` strictly out of `final_route_match`/`identity_preserved`/`ordering_consistent`. **[Agent: python-backend]**
  - [ ] Extend `rules.build_ops` to handle airbnb (routes identically to hotel), venue-with-city (event analogous to accommodation), and supplementary-without-place (single `add_unattached_document`). Update the pending-projection ledger to include a `venues` bucket. **[Agent: python-backend]**
  - [ ] Extend `expected-route.schema.json` with `stops[].venues` and top-level `unattachedDocuments`; update `ExpectedRoute` Pydantic models in `corpus.py`. **[Agent: python-backend]**
  - [ ] Unit tests in `backend/tests/spikes/test_rules_new_shape.py`: airbnb routed like hotel; venue-with-city attached to right stop; supplementary-without-place lands in `unattached_documents`; supplementary-with-city attaches as a venue/document on the stop without altering route shape; `add_unattached_document` does not mutate `stops[]` / `transits[]` / id counters. **[Agent: python-backend]**
  - [ ] **Verify:** `just test-corpus` green; `just spike-engine-algo` still 192/192 (no new doc types in this corpus); `cd backend && uv run pytest tests/spikes/test_rules_new_shape.py` green; `just lint` clean. **[Agent: python-backend]**

- [ ] **Slice 6: Adapter from `ExtractedFields` to `Fragment`**
  - [ ] Create `backend/spikes/integration/__init__.py` and `adapter.py` with `extracted_fields_to_fragment(fields: ExtractedFields, *, source_document_id: str) -> Fragment`. Pure mapping, no I/O. **[Agent: python-backend]**
  - [ ] Map: `document_type` (snake → kebab), datetimes (local ISO → UTC datetime), structured place lists (cities, stations, accommodations, venues), travelers, prices, qrCodes, pdfKind. **[Agent: python-backend]**
  - [ ] Unit tests in `backend/tests/spikes/test_adapter.py` covering all six document kinds × single-leg / return / multi-traveler / missing optional datetimes / venue-with-city / venue-without-city / supplementary-with-city / supplementary-without-place. Asserts the resulting `Fragment` validates against `extracted-fragment.schema.json`. **[Agent: python-backend]**
  - [ ] **Verify:** `cd backend && uv run pytest tests/spikes/test_adapter.py` green; `just lint` clean. **[Agent: python-backend]**

- [ ] **Slice 7: Integration runner + one trip end-to-end (live Bedrock)**
  - [ ] Create `corpus/integration/README.md` documenting the coverage matrix (placeholder for now). **[Agent: python-backend]**
  - [ ] Create `corpus/integration/01-straight-3city-1pax-air/` with `manifest.json` (3 layer-1 air PDFs from the existing corpus chained as a 3-city straight journey, single traveler) + `expected-route.json`. **[Agent: python-backend]**
  - [ ] Create `backend/spikes/integration/runner.py` (CLI with `--trip`, `--no-route-check`, `--json-report`) and `backend/spikes/integration/report.py` (summary + diff rendering). **[Agent: python-backend]**
  - [ ] Add `just integration *args` recipe (mirrors `just extract-pdf` isolated-venv pattern). **[Agent: python-backend]**
  - [ ] `backend/tests/spikes/test_integration_runner.py`: stub-extractor tests for discovery, the unreadable-document path, the `--trip` flag, the JSON report shape. **[Agent: python-backend]**
  - [ ] **Verify:** `cd backend && uv run pytest tests/spikes/test_integration_runner.py` green; `just integration --trip 01-straight-3city-1pax-air` runs live Bedrock and reports PASS; `runs/<ts>-integration/report.json` is well-formed; `just lint` clean. **[Agent: bedrock-llm]**

- [ ] **Slice 8: Coverage matrix — core dimensions**
  - [ ] Add trips: `02–05` per-mode straight-line (rail, bus, mixed) + `06–09` return journeys (air, rail, bus, mixed), single traveler. **[Agent: python-backend]**
  - [ ] Add trips: `10` two-pax-air-with-hotels; `11` airbnb-leg-2pax-rail; `12` same-city-two-airports (Paris CDG + Paris ORY collapse). **[Agent: python-backend]**
  - [ ] Add trips: `13` divergent-travelers-mid-trip; `14` supplementary-voucher (with city); `15` supplementary-parking (without place); `16` sightseeing-venue-on-stop. **[Agent: python-backend]**
  - [ ] Update `corpus/integration/README.md` coverage matrix with each trip's dimensions. **[Agent: python-backend]**
  - [ ] **Verify:** `just integration` PASS rate is 16/16; failures (if any) surface per-trip diffs; `just lint` clean. **[Agent: bedrock-llm]**

- [ ] **Slice 9: Scan PDFs, unreadable PDF, real-shape buffer — final 20–30 set + 100% gate**
  - [ ] Add trips: `17–18` scan-pdf-mixed-trip-{air,rail} (each picks at least one rasterized PDF from `corpus/pdf/layer1/`). **[Agent: python-backend]**
  - [ ] Add trip: `19` unreadable-pdf-in-trip (includes one PDF flagged `expect_unreadable: true`; expected-route is built from the rest). Tweak the runner if needed so the `expect_unreadable` branch is exercised against a live Bedrock failure. **[Agent: python-backend]**
  - [ ] Add 5–10 "real-shape" trips (`20`–`30`) mirroring common European itineraries with a mix of doc types, scans, multi-traveler, and accommodations. **[Agent: python-backend]**
  - [ ] Finalise the README coverage matrix. **[Agent: python-backend]**
  - [ ] **Verify:** `just integration` reaches 20–30/20–30 PASS — the spec's 100% hard gate. JSON report includes per-trip extraction path mix and per-PDF token cost; `just lint` clean. **[Agent: bedrock-llm]**

- [ ] **Slice 10: Cross-schema validation upgrade (DUS-31 done-when)**
  - [ ] In `corpus/pdf/validate.py`, replace the documented mapping between `expected-fields.schema.json` and `extracted-fragment.schema.json` with a real `jsonschema.validate(sample_fragment, fragment_schema)` step over a fixture fragment derived from one layer-1 extracted-fields payload. **[Agent: python-backend]**
  - [ ] **Verify:** `just test-corpus` runs the new cross-schema validation step and is green; `just lint` clean. **[Agent: python-backend]**
