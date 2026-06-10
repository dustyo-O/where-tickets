# Functional Specification: Document-to-Route Integration

- **Roadmap Item:** Route Engine (Foundation) — bridging Document Ingest and the Route Engine so the engine consumes the facts the extractor actually produces.
- **Linear:** [DUS-31](https://linear.app/dusty-work/issue/DUS-31/extend-engine-fragment-schema-for-cities-stations-accommodations)
- **Status:** Draft
- **Author:** Alexander Shleyko

---

## 1. Overview and Rationale (The "Why")

Two halves of the trip pipeline are already in place:

- **The extractor** (Document Ingest) reads an uploaded travel PDF and produces a structured record of what is printed on it — the document kind, every city named, every transit station, every accommodation, every venue, every date and time, every traveler, every price. It currently grades 149/150 on the curated PDF corpus.
- **The route engine** (Route Engine Foundation) takes structured travel facts, one document at a time, and assembles them into an ordered route of cities with the documents attached to each leg. It grades 192/192 on its own scenario corpus.

The problem is that **the engine speaks an older, narrower language than the extractor**. The engine knows about four document kinds; the extractor recognises six. The engine identifies every location by one short airport-style code that conflates "the city" with "the specific airport"; the extractor produces printed city names plus structured station, accommodation, and venue entries. The engine has no place for an Airbnb stay, a sightseeing booking, a parking voucher, a price, or a QR code; the extractor produces them. Today, the only way the engine sees a real PDF is through a hand-translated fragment — there is no end-to-end path from "the traveler uploaded a PDF" to "the trip's route updated correctly".

**This work closes that gap.** It extends what the route engine accepts so it can route directly on the extractor's output — every document kind, every routable place, every traveler. After this work, dropping a stack of real PDFs into the trip produces the correct route automatically, with the documents that don't change the route (prices, QR codes, the few document kinds that don't carry a location) still attached to the trip rather than dropped.

**Success bar.** A curated set of ~20–30 multi-document trips, built from the existing PDF corpus, must produce the expected route on every scenario — a 100% hard gate. The engine's prior 192-scenario corpus must continue to pass at 192/192 alongside it, so the language change does not silently regress the engine's own behaviour.

**Why this matters now.** Without it, every later piece of the product — Trip Route View, multi-PDF uploads, on-the-day ticket surfacing — is blocked behind hand-conversion of the extractor's output. With it, the full path "upload PDFs → see a correct trip" works for the first time.

---

## 2. Functional Requirements (The "What")

### 2.1 The trip is built from real PDFs end-to-end

- **Dropping a set of travel PDFs into a trip produces a correct route without any manual translation between the two halves.**
  - Acceptance Criteria:
    - [ ] Given a trip of one or more travelers and a set of travel PDFs, when the PDFs are handed to the system, then the resulting route matches the expected route for that trip exactly — same ordered sequence of cities, same transit segments between them, same accommodations and venues attached to the right city, same travelers attached to each stop and transit.
    - [ ] No manual rewriting, renaming, or re-keying of the extractor's output is required between the two halves of the pipeline.

### 2.2 Every routable place ends up on the route

- **Cities, transit stations, accommodations, and venues with a location all participate in routing.**
  - Acceptance Criteria:
    - [ ] An air, rail, or bus ticket places its origin and destination on the route as the cities printed on the document.
    - [ ] A hotel or Airbnb booking attaches the stay to the city printed on the document.
    - [ ] A venue that carries a city (e.g. a sightseeing booking in Lisbon, a parking voucher for Madrid) is attached to that city's stop on the route.
    - [ ] Multiple stations within the same city (e.g. two different Paris airports across two PDFs of the same trip) collapse to a single Paris stop on the route — the route is a sequence of cities, not of stations.

### 2.3 Document kinds the engine could not route on before are now supported

- **All six document kinds the extractor recognises are accepted by the route engine.**
  - Acceptance Criteria:
    - [ ] An Airbnb booking is routed exactly like a hotel booking — attached to the right city with the correct check-in / check-out window.
    - [ ] A supplementary document (voucher, sightseeing ticket, parking voucher) is accepted by the engine; if it carries a city it is attached to that city's stop, otherwise it is attached to the trip without changing the route.
    - [ ] The four document kinds the engine already supports (air, rail, bus, hotel) continue to behave exactly as they do today — no regression on their existing behaviour.

### 2.4 Facts that don't change the route are still attached, not dropped

- **Prices, QR codes, and document kinds without a routable location are preserved on the trip even though they do not affect the route's shape.**
  - Acceptance Criteria:
    - [ ] Every price printed on a routed document is attached to the document on the trip and is retrievable when the document is inspected.
    - [ ] QR code payloads (whatever the extractor currently captures) are preserved on the document on the trip — this work does not change the QR extraction policy, it just ensures the bridge does not silently drop them.
    - [ ] A supplementary document with no routable location stays attached to the trip as a "no-location document" without altering the route's sequence of cities.

### 2.5 Multi-traveler trips are routed per traveler

- **A trip that includes more than one traveler produces a route in which every stop and transit shows the right travelers.**
  - Acceptance Criteria:
    - [ ] Given a trip with two travelers where every document names both travelers, the resulting route shows both travelers on every stop and transit.
    - [ ] Given a trip where one PDF covers one traveler and another covers a second traveler on the same leg, each transit on the route shows the traveler the document was issued to, and the leg as a whole is not duplicated.
    - [ ] Given a trip where one traveler diverges from the others mid-trip, the route reflects the divergence — the divergent traveler's stops and transits are attributed only to them.

### 2.6 PDFs that could not be read do not break the route

- **A trip that includes a PDF the extractor could not read still produces the correct route from the rest, and surfaces the unreadable PDF without dropping it.**
  - Acceptance Criteria:
    - [ ] Given a trip of N PDFs where one PDF was marked "couldn't be read" by the extractor, the route is built from the remaining N − 1 PDFs as if the unreadable one were not part of the trip.
    - [ ] The unreadable PDF still appears as an entry on the trip, marked "couldn't be read", with no extracted facts attached. _(The exact visual treatment of this entry is owned by the Trip Route View spec, not by this one.)_
    - [ ] The presence of an unreadable PDF in the set never causes a routable PDF in the same set to be dropped or mis-routed.

### 2.7 Coverage matrix for the end-to-end test set

- **The end-to-end test set covers the dimensions that vary in real trips, drawn from the existing PDF corpus.**
  - Acceptance Criteria:
    - [ ] The set contains 20–30 multi-document trips. Each trip is built from two or more PDFs taken from the existing PDF corpus.
    - [ ] Every one of the six document kinds appears in at least one trip (air, rail, bus, hotel, Airbnb, supplementary).
    - [ ] At least one trip is a straight-line journey through three or more cities.
    - [ ] At least one trip is a return journey (out and back).
    - [ ] At least one trip includes a same-city stop served by two different stations (e.g. two different airports in the same city).
    - [ ] Two or three trips include more than one traveler.
    - [ ] At least one trip mixes text-bearing PDFs with at least one scan-style PDF.
    - [ ] At least one trip includes a PDF the extractor cannot read, to exercise the behaviour in §2.6.
    - [ ] Each trip in the set ships with its expected route written out, so the comparison is exact and reproducible.

### 2.8 The engine's existing scenarios still pass

- **Extending the engine's input language must not regress its existing behaviour.**
  - Acceptance Criteria:
    - [ ] After this work lands, the engine's prior 192-scenario corpus continues to pass at 192/192.
    - [ ] If the scenarios in that corpus need to be re-expressed in the new richer language to still pass, that is acceptable — the gate is that all 192 scenarios still pass end-to-end, not that the corpus files are byte-identical.

### 2.9 The integration test set can be run on demand

- **An engineer can run the full end-to-end set on demand and read the result.**
  - Acceptance Criteria:
    - [ ] A single command runs the full end-to-end set against the live extractor and route engine and prints a pass/fail summary plus per-scenario detail for any failures.
    - [ ] A failure surfaces which trip failed, what the expected route was, and what the produced route was, in enough detail that an engineer can investigate without rerunning.
    - [ ] The same command can be pointed at a single trip for fast iteration when investigating one failure.

---

## 3. Scope and Boundaries

### In-Scope

- Extending what the route engine accepts so that the extractor's output feeds it directly, with no manual translation step.
- Routing on every routable place the extractor reports — cities, transit stations, accommodations, venues — with same-city stations collapsing to one stop on the route.
- Accepting all six document kinds the extractor recognises (air, rail, bus, hotel, Airbnb, supplementary), including the two that the engine could not previously route on.
- Preserving prices, QR codes, and no-location documents on the trip even though they do not change the route.
- Per-traveler routing on trips that involve more than one traveler, including divergent paths.
- A curated 20–30-scenario, multi-PDF end-to-end test set, drawn from the existing PDF corpus, with a 100% hard gate.
- A coexistence guarantee that the engine's prior 192-scenario corpus continues to pass at 192/192.
- A single on-demand command to run and inspect the end-to-end set.

### Out-of-Scope

- **Other roadmap items, handled in separate specs:** Trip Route View; PDF Upload (the upload UX itself); Sign-Up & Login; all of Phase 2 (Custom Accommodations, Custom Transportation, Completeness Checks, Context-Aware Ticket Surfacing, Offline Access); all of Phase 3 (Travelspace Sharing, Edge-Case Polish, App Store & Play Store Publication).
- **Other slices of the Route Engine roadmap line:** the visible Trip Route View; further engine quality tuning beyond what this slice requires.
- **Other slices of the Document Ingest roadmap line:** QR / barcode payload decoding from image regions (tracked in DUS-33); the upload UX; further extractor accuracy tuning.
- **Within this topic, explicitly excluded:**
  - Changes to how documents are extracted — this work consumes what the extractor produces today and does not redefine its accuracy bar.
  - The visual treatment of the route, the "couldn't be read" entry, or any per-traveler view — those belong to Trip Route View.
  - Routing on facts the extractor does not produce reliably today (e.g. the QR-decoded payload), since that work is tracked separately under DUS-33.
  - Automated / scheduled runs of the end-to-end set (CI gates, nightly runs) — the engineer runs the set on demand for now.
  - Performance, latency, or cost targets for the end-to-end run — measured implicitly via the existing extractor and engine spike numbers; no specific number is set for v1.
  - Layer 2 now holds generator-emitted multi-PDF trip bundles (`corpus/pdf/layer2/<trip-slug>/`) consumed by the integration runner. Real, locally-collected PDFs remain out of scope for v1; if added later they fit into the same Layer-2 tree.
  - Non-English PDFs and loop topologies — same scope boundary as the upstream extractor corpus.
