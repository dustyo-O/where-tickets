# Functional Specification: Mock-Document Corpus

- **Roadmap Item:** Mock-Document Corpus (Phase 1 → Route Engine Foundation)
- **Status:** Draft
- **Author:** Dusty

---

## 1. Overview and Rationale (The "Why")

The Mock-Document Corpus is the curated test set that proves the PDF pipeline (AI extraction + route engine) is accurate enough for v1. It exists so engine quality is measurable, reproducible, and protected from regressions as both the extractor and the route engine evolve.

It sits alongside, not on top of, the existing 192-scenario engine-spike corpus. That earlier corpus is a synthetic *fragment* set that proves route assembly works on already-extracted data. The Mock-Document Corpus works one layer up — at the **PDF** level — so it closes the loop on the v1 success bar:

> ≥ 99% of uploaded documents are parsed into the correct city and placed at the correct position in the route with no manual correction.

The corpus has two layers:

- **Layer 1 — Generated fake PDFs** (kept in the shared codebase): look-alike fake tickets and bookings authored to mimic real-world layouts (made-up brand logos, marketing footers, terms blurbs, voucher codes, multiple QRs). Layer 1 isolates and proves the **extraction** step — that the right fields come out of a PDF in the shape the engine needs.
- **Layer 2 — Real PDFs** (local-only, never committed to the shared codebase): actual tickets and bookings collected from real trips. Layer 2 proves **extraction** works on real-world documents, including providers, layouts, and scan-style PDFs no one on the team would realistically invent.

**Why two layers:** Layer 1 catches extraction bugs deterministically and is safe to share. Layer 2 surfaces failures the team would never anticipate, but the underlying PDFs cannot be shared because they carry real personal data (names, passport numbers, payment details).

**Success for this work** is that the engineer working on the engine can run the corpus, see a per-scenario PASS / FAIL with a drill-down expected-vs-actual diff, and see an overall accuracy percentage — and that the corpus has enough coverage that meeting the 99% bar against it is a credible proxy for meeting it against real customer documents.

---

## 2. Functional Requirements (The "What")

### 2.1 Layer 1 — Generated Fake PDFs

- **The corpus must contain ~50 generated trips totalling ~150 PDFs.**
  - **Acceptance Criteria:**
    - [ ] The shared codebase holds 45–55 fake trip scenarios and 135–165 individual fake PDFs in total.

- **Every document type the product accepts must be represented.**
  - **Acceptance Criteria:**
    - [ ] At least one fake PDF exists for each of: air ticket, rail ticket, bus ticket, hotel booking, Airbnb booking, supplementary (voucher / sightseeing / parking).

- **Generated fake PDFs must look like real-world tickets.**
  - **Acceptance Criteria:**
    - [ ] Each fake PDF carries a fictional brand/provider logo, realistic field placement, and noise of the kind found on real tickets: marketing footer, terms-and-conditions block, voucher codes, or multiple QR codes where appropriate to the document type.

- **All Layer 1 PDFs are in English for v1.**
  - **Acceptance Criteria:**
    - [ ] No fake PDF contains non-English copy.

- **The corpus must cover these scenario shapes:**
  - **Acceptance Criteria:**
    - [ ] At least three scenarios are multi-leg trips with 3 or more cities in sequence.
    - [ ] At least three scenarios include a PDF covering two or more travelers on a single ticket/booking.
    - [ ] At least three scenarios are return tickets where one PDF covers both outbound and return legs.
    - [ ] At least three scenarios include a standalone supplementary document (voucher, sightseeing ticket, or parking confirmation) attached to a city.

- **Every Layer 1 PDF ships with its expected extracted fields (the ground truth for extraction).**
  - **Acceptance Criteria:**
    - [ ] For each fake PDF there is an expected-fields record specifying: document type, all cities mentioned, all dates and times, all travelers named, all prices, and all QR codes present.
    - [ ] When extraction runs against the PDF and produces those same fields, the scenario PASSES for extraction; any deviation in any field counts as a FAIL.

### 2.2 Layer 2 — Real PDFs (Local-Only)

- **Real PDFs are never committed to the shared codebase.**
  - **Acceptance Criteria:**
    - [ ] The folder holding Layer 2 PDFs is excluded from version control.
    - [ ] No real personal documents ever land in the project history.

- **The team can drop a real PDF into a known local folder alongside its expected fields, and the next corpus run picks it up.**
  - **Acceptance Criteria:**
    - [ ] When a new real PDF and its expected-fields file are placed in the Layer 2 folder, running the corpus includes that PDF automatically, without any code change.

- **Every Layer 2 PDF ships with its expected extracted fields (the ground truth for extraction).**
  - **Acceptance Criteria:**
    - [ ] Each Layer 2 PDF has an expected-fields record specifying: document type, all cities mentioned, all dates and times, all travelers named, all prices, and all QR codes present.
    - [ ] When extraction runs against the PDF and produces those same fields, the scenario PASSES; any deviation in any field counts as a FAIL.

- **Layer 2 has no fixed size target for v1.**
  - **Acceptance Criteria:**
    - [ ] Layer 2 grows organically as real PDFs are collected; the run does not error or fail if Layer 2 is empty on a given machine.

### 2.3 Running the Corpus

- **The engineer can run the full corpus on demand, and is expected to run it on every engine change.**
  - **Acceptance Criteria:**
    - [ ] A single command runs every Layer 1 scenario and every Layer 2 scenario present on the local machine.
    - [ ] Every scenario produces one of two outcomes: PASS or FAIL.

- **The run produces a global accuracy report, broken out by layer.**
  - **Acceptance Criteria:**
    - [ ] At the end of every run, the engineer sees: total scenarios, scenarios passed, scenarios failed, and overall accuracy %.
    - [ ] Layer 1 and Layer 2 accuracy are reported separately so each layer's quality is visible at a glance.

- **For any failed scenario, the engineer can drill down into the expected-vs-actual diff.**
  - **Acceptance Criteria:**
    - [ ] Each failed scenario lists which fields did not match.
    - [ ] The diff shows expected and actual side-by-side for each mismatch.

---

## 3. Scope and Boundaries

### In-Scope

- A two-layer corpus: Layer 1 (generated, committed) + Layer 2 (real, local-only, not committed).
- ~50 trips / ~150 PDFs in Layer 1 at v1; Layer 2 grows organically.
- All six document types in Layer 1: air, rail, bus, hotel, Airbnb, supplementary.
- Look-alike realism in Layer 1 (fictional logos, marketing, terms, multiple QRs).
- English-only PDFs at v1.
- Per-PDF expected-fields ground truth for both Layer 1 and Layer 2.
- Required scenario shapes: multi-leg (3+), multi-traveler bookings, return tickets, standalone supplementary docs.
- A full-corpus run command that produces per-scenario PASS / FAIL, overall accuracy %, layer-by-layer accuracy %, and per-failure expected-vs-actual diffs.

### Out-of-Scope

- **Other roadmap items, handled in separate specs:** Trip Route View, PDF Upload, AI Document Understanding, Sign-Up & Login, all of Phase 2 and Phase 3.
- **Within this topic, explicitly excluded for v1:**
  - Non-English PDFs (added later as the engine matures).
  - Loop topologies that pass through the same city twice on different days, and same-day connecting flights.
  - Shared or cloud-based storage for Layer 2 (real PDFs stay strictly local).
  - Automated / scheduled corpus runs (CI gates, nightly runs). At v1 the engineer runs it on demand.
  - A built-in PDF generation tool — Layer 1 PDFs may be authored by any means (manual, scripted, generator), but the spec does not require a tool to live alongside the corpus.
  - Reusing or migrating the existing 192-scenario fragment corpus from the engine spike — it stays as a separate, untouched artifact.
