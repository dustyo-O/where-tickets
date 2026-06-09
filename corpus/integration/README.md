# Integration trips: end-to-end document-to-route

DUS-31 / spec 007. Each trip directory under `corpus/integration/<slug>/`
pairs a `manifest.json` (ordered list of source PDFs from `corpus/pdf/`,
plus the trip's travelers and any `expect_unreadable: true` flags) with an
`expected-route.json` describing the final `WorkingRoute` the engine should
produce after every readable PDF in manifest order.

The runner that drives these trips lives at
`backend/spikes/integration/runner.py`; invoke it via `just integration`.

## How the catalogue is built

Trips are emitted by the **integration trip-bundle generator** at
`corpus/integration/generator/`. Each trip in `generator/catalog.py` is
described as an ordered list of primitives (one air leg / one hotel stay /
one supplementary venue / etc.); the composer walks them and emits:

- One PDF per primitive, under
  `corpus/pdf/layer2/<trip-slug>/<NN>-<docname>.pdf`, with a sibling
  `<NN>-<docname>.expected-fields.json` so the **layer-2 PDF runner
  validates the lot for free**.
- The trip's `manifest.json` + `expected-route.json` under
  `corpus/integration/<trip-slug>/`.

Regenerate with `just regen-integration-corpus`. The output is deterministic —
two runs produce byte-identical JSON. PDFs may shift only via renderer-level
variance (matches the layer-1 generator's behaviour).

## Trip catalogue

| Slug | Doc kinds | Travelers | PDFs | Coverage notes |
|---|---|---|---|---|
| `01-air-out-hotel-back-paris-lisbon-1pax` | air + hotel + air | 1 | 3 | Phase-1 baseline: air out, Lisbon hotel, air back. |
| `02-air-return-1pax-frankfurt-amsterdam` | air | 1 | 1 | Single-PDF compact-form return air. |
| `03-rail-out-hotel-back-1pax-paris-lisbon` | rail + hotel + rail | 1 | 3 | Per-mode rail with hotel. |
| `04-bus-return-1pax-madrid-rome` | bus | 1 | 1 | Single-PDF compact-form return bus. |
| `05-air-rail-3city-1pax-paris-frankfurt-amsterdam` | air + rail | 1 | 2 | Mixed-mode straight 3-city. |
| `06-air-straight-2pax-madrid-rome` | air | 2 | 1 | Single-leg air, 2 travelers. |
| `07-air-out-hotel-back-2pax-london-vienna` | air + hotel + air | 2 | 3 | 2-traveler with hotel. |
| `08-air-out-airbnb-back-1pax-berlin-prague` | air + airbnb + air | 1 | 3 | Airbnb variant. |
| `09-same-city-two-airports-1pax-paris-lisbon` | air + air | 1 | 2 | Paris CDG outbound, Paris ORY inbound — same-city different-station collapse. |
| `10-divergent-travelers-mid-trip-2pax-paris-frankfurt-berlin` | air + air | 2 | 2 | Shared first leg; one traveler continues alone. |
| `11-air-supplementary-venue-1pax-madrid-rome` | air + supplementary | 1 | 2 | Supplementary doc carrying one venue with a city. |
| `12-air-supplementary-no-location-1pax-madrid-rome` | air + supplementary | 1 | 2 | Supplementary doc with no routable place → `unattachedDocuments[]`. |
| `13-air-hotel-venue-1pax-london-paris` | air + hotel + supplementary | 1 | 3 | Venue routed onto an existing hotel stop. |
| `14-air-scan-mix-1pax-frankfurt-amsterdam` | air (scan) + hotel + air | 1 | 3 | At least one rasterized PDF in the mix. |
| `15-air-with-unreadable-pdf-1pax-madrid-rome` | unreadable + air | 1 | 2 | One PDF flagged `expect_unreadable: true`; trip builds from the rest. |
| `16-air-3city-two-hotels-1pax-paris-berlin-prague` | air × 3 + hotel × 2 | 1 | 5 | Real-shape itinerary: 3 cities, 2 hotels. |
| `17-rail-3city-hotel-1pax-paris-frankfurt-amsterdam` | rail + hotel + rail | 1 | 3 | Real-shape rail 3-city with hotel. |
| `18-air-rail-airbnb-1pax-madrid-rome-florence` | air + airbnb + rail | 1 | 3 | Real-shape air + rail + airbnb. |
| `19-air-hotel-venue-2pax-london-paris` | air + hotel + supplementary | 2 | 3 | 2-traveler real-shape with venue. |
| `20-air-3city-return-1pax-paris-berlin-vienna` | air × 3 + hotel × 2 | 1 | 5 | Real-shape 3-city return. |

20 trips, 52 PDFs total. Full live integration runs in ~3 minutes on Bedrock.

## Coverage map vs functional spec §2.7

- All six document kinds appear: air, rail, bus, hotel, airbnb, supplementary.
- Straight-line 3+ cities: 05, 16, 17, 18, 20.
- Return journeys: 01, 02, 03, 04, 07, 08, 09, 13, 14, 15, 16, 19, 20.
- Same-city stop with two different stations: 09.
- Multi-traveler: 06, 07, 10, 19.
- Scan-style PDF mix: 14.
- Unreadable PDF: 15.
- Each trip ships its expected-route.json, so the comparison is exact and
  reproducible.

## Iteration

- Single trip: `just integration --trip <slug>` — fast feedback against
  live Bedrock.
- Adapter-only sanity: `just integration --no-route-check` — skip scoring.
- Regen one trip: `just regen-integration-corpus --trip <slug>`.
- Regen all: `just regen-integration-corpus`.
