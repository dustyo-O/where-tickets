# Functional Specification: AI Document Understanding — PDF Extraction

- **Roadmap Item:** AI Document Understanding (Phase 1 → Document Ingest). This spec covers the slice scheduled under DUS-32; any later slice of the same roadmap line will get its own spec.
- **Status:** Draft
- **Author:** Dusty

---

## 1. Overview and Rationale (The "Why")

The traveler arrives with a stack of PDFs from many providers — airlines, rail, bus, hotels, Airbnb, vouchers — and expects to see a clean trip without retyping anything. Today they don't have that; they juggle email, screenshots, and the source PDFs themselves.

This capability is the second half of the Document Ingest pipeline. PDF Upload is the first half (the traveler picks the file); AI Document Understanding is what happens once that file is in the system: the app reads the PDF, figures out what kind of document it is, and pulls out every fact printed on it — cities, transit stations, accommodations, venues, dates and times, travelers, prices, QR codes. The route engine then consumes those facts to build the trip.

**Why this slice matters now.** The corpus that grades extraction quality (~150 representative PDFs across all six document types, including roughly 15 % scan-style PDFs) already exists, but until an extractor is wired into it the corpus run reports nothing usable. This work wires an extractor in and proves it clears the v1 quality bar.

**Success bar.** ≥ 99 % of corpus PDFs come back with every printed field correctly captured. This is measured by running the corpus on demand and reading the overall accuracy percentage; the spec is not "done" until that percentage is ≥ 99 %. The number matches the v1 product success metric ("≥ 99 % of uploaded documents parsed into the correct city and placed at the correct position in the route with no manual correction").

**Out-of-bar but important.** When a PDF genuinely cannot be read after every internal attempt, the traveler must not silently lose it — the PDF must end up as a "couldn't be read" entry in the trip so the traveler knows to look at it themselves.

---

## 2. Functional Requirements (The "What")

### 2.1 Reading an uploaded PDF

- **The system processes every uploaded PDF and produces a structured record of the facts printed on it.**
  - Acceptance Criteria:
    - [ ] After a PDF is uploaded, the system attempts to extract: the document type, every city named on it, every transit station / airport, every accommodation, every venue, every date and time, every traveler named, every price, and every QR code.
    - [ ] Each value reflects what is literally printed on the document (e.g. the city name as printed, not normalized to a code).
    - [ ] All six v1 document types are supported: air ticket, rail ticket, bus ticket, hotel booking, Airbnb booking, supplementary (vouchers, sightseeing tickets, parking).

- **Scan-style PDFs (no extractable text layer — just an image of the document) are supported.**
  - Acceptance Criteria:
    - [ ] A PDF that looks identical to a normal ticket but was produced as an image scan still has its fields extracted; the traveler does not need to upload it any differently from a regular ticket.

- **Multi-page documents are supported.**
  - Acceptance Criteria:
    - [ ] A multi-page PDF (e.g. a hotel booking that spans two pages) is processed in full and produces a single structured record covering both pages.

- **Return tickets and multi-traveler bookings on a single PDF are supported.**
  - Acceptance Criteria:
    - [ ] A PDF that covers both outbound and return legs produces all relevant stations and datetimes for both legs.
    - [ ] A PDF that names multiple travelers on the same ticket/booking captures every traveler.

### 2.2 Accuracy bar

- **Across the existing 150-scenario corpus, the system reaches ≥ 99 % overall accuracy.**
  - Acceptance Criteria:
    - [ ] Running the full Layer 1 corpus on demand reports an overall accuracy of ≥ 99 %.
    - [ ] On every scenario, the produced fields match the ground-truth fields field-for-field (any deviation counts as a fail for that scenario, per the corpus's existing PASS / FAIL definition).
    - [ ] Scan-style PDFs in the corpus pass at the same accuracy bar as text-bearing PDFs.

### 2.3 When a PDF cannot be read

- **A PDF is only flagged as "couldn't be read" after every internal attempt has been exhausted.**
  - Acceptance Criteria:
    - [ ] If the system can produce any structured record for the PDF, the PDF is treated as successfully extracted (and the corpus / trip use those fields).
    - [ ] Only when every internal attempt has failed to produce a structured record is the PDF marked as "couldn't be read".

- **An unreadable PDF is preserved in the trip as a "couldn't be read" entry, not silently dropped.**
  - Acceptance Criteria:
    - [ ] The PDF still appears in the trip as an entry attached to the traveler's documents, marked as "couldn't be read", with no extracted fields.
    - [ ] The exact visual treatment of the "couldn't be read" entry (icon, copy, position in the trip, retry behaviour) is owned by the Trip Route View / PDF Upload specs and is not defined here.

### 2.4 Observability for the engineer

- **The engineer can tell, for every PDF in a corpus run, which internal reading path was used (printed text vs. image-based reading).**
  - Acceptance Criteria:
    - [ ] The corpus run summary surfaces a path mix (how many PDFs were read via printed text vs. via image).
    - [ ] Text-bearing PDFs in the corpus are expected to land on the text path; scan-style PDFs are expected to land on the image path.
    - [ ] If a text-bearing PDF unexpectedly falls back to the image path, this is visible in the run output so the engineer can investigate.

- **The engineer can run extraction against a single PDF on demand to debug a specific scenario.**
  - Acceptance Criteria:
    - [ ] A single command takes one PDF path and prints the extracted fields and the reading path that was used.
    - [ ] No corpus run is required — this is a one-shot inspection for ad-hoc debugging.

---

## 3. Scope and Boundaries

### In-Scope

- Extracting structured fields from a single uploaded PDF, for all six v1 document types, including scan-style PDFs and multi-page PDFs.
- Reaching ≥ 99 % overall accuracy on the existing 150-scenario Layer 1 corpus, as a hard gate for "done".
- Producing a "couldn't be read" signal when every internal attempt fails, so downstream views can present the PDF without losing it.
- Surfacing, in the corpus run output, which reading path was used per PDF, for engineer observability.
- A one-PDF on-demand debug recipe so the engineer can inspect a single scenario.

### Out-of-Scope

- **Other roadmap items, handled in separate specs:** Trip Route View; PDF Upload; Sign-Up & Login; all of Phase 2 and Phase 3.
- **Other slices of the AI Document Understanding roadmap line:** any future extractor work beyond what this slice covers (further accuracy tuning, additional readers, secondary detection passes) will get its own spec.
- **Within this topic, explicitly excluded for v1:**
  - The visual treatment of the "couldn't be read" entry in the trip (icon, copy, position, retry button) — owned by Trip Route View / PDF Upload.
  - A user-perceived latency target — no specific turnaround number is set for v1; cost and latency are tracked internally for later tuning.
  - Accuracy targets on Layer 2 (real, locally-collected PDFs) — Layer 2 grows organically and has no fixed accuracy bar for v1; this spec is graded on Layer 1 only.
  - Non-English PDFs — same scope boundary as the corpus.
  - Loop topologies and same-day connecting flights — same scope boundary as the corpus.
  - Manual editing of extracted fields by the traveler — separate downstream concern.
  - Automated / scheduled corpus runs (CI gates, nightly runs) — the engineer runs the corpus on demand.
