# Integration trips: end-to-end document-to-route

DUS-31 / spec 007. Each trip directory under `corpus/integration/<slug>/`
pairs a `manifest.json` (ordered list of source PDFs from
`corpus/pdf/layer1/scenarios/`, plus the trip's travelers and any
`expect_unreadable: true` flags) with an `expected-route.json` describing the
final `WorkingRoute` the engine should produce after every readable PDF in
manifest order.

The runner that drives these trips lives at
`backend/spikes/integration/runner.py`; invoke it via `just integration`.

## Trip catalogue

| Slug | Doc kinds | Travelers | Notes |
|---|---|---|---|
| `01-air-return-1pax-paris-lisbon` | air | 1 (`Ines Marques`) | Single-PDF return Paris→Lisbon→Paris. Exercises the adapter, the engine's compact-form station pairing, and `final_route_match` against a real-PDF route. |

## Layer-1 limitation (deferred multi-PDF chaining)

Every PDF under `corpus/pdf/layer1/scenarios/` is single-purpose: each scenario
covers exactly one PDF — one origin/destination pair, its own randomly chosen
travelers, and its own randomly chosen dates. No two layer-1 PDFs share
endpoints, and the per-scenario traveler sets and datetimes are independently
sampled, so chaining two layer-1 PDFs into one trip would describe
**different people travelling at incompatible times** — the resulting route
wouldn't be coherent and `final_route_match` would (correctly) fail.

Multi-PDF trips therefore land in a later slice — likely via Layer 2 (real
collected PDFs) or a small generator extension that emits a coherent multi-PDF
trip (shared travelers, chronologically ordered dates) into
`corpus/integration/<slug>/`. Slice 7 lands the runner + the first
single-PDF trip so the end-to-end pipeline is exercised on real Bedrock; the
remaining trips in the coverage matrix (per spec §2.7) will be added once the
multi-PDF corpus extension is in place.
