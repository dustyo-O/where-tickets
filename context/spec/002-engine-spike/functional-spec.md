# Functional Specification: Engine Spike & Decision

- **Roadmap Item:** Route Engine (Foundation) → Engine Spike & Decision
- **Linear:** [DUS-19](https://linear.app/dusty-work/issue/DUS-19/spike-a-llm-driven-route-updater-on-bedrock)
- **Status:** Completed
- **Author:** Alexander Shleyko

---

## 1. Overview and Rationale (The "Why")

The route engine is the heart of the product and its biggest unknown: turning a pile of arbitrarily-ordered travel documents into one coherent, ordered sequence of cities. Before we build the Trip Route View on top of it, we need confidence in **which approach to commit to for v1** — an AI-driven engine or a hand-written algorithmic one.

Time has shifted our intuition toward AI: real itineraries are messy (round-trips, loops, repeated cities, partial fragments, mixed transport modes), and an LLM may handle ambiguity better than rules. But "AI" is not one thing — Claude Opus, Sonnet, and Haiku have very different cost, latency, and quality profiles, and the right choice is not obvious.

This spike answers three questions, in order:
1. Can an AI engine assemble routes correctly enough across our corpus to be the v1 engine?
2. If yes, which model gives the best balance of accuracy, cost, and speed?
3. Does the AI engine respect the two non-negotiable behaviors we've already committed to in the product: **append-don't-rebuild** (existing route-cities and the user-added context attached to them must survive every new document) and **gap-tolerance** (a route with one or more missing segments is still a valid, correctly-ordered route).

**Success of the spike** is a clear go/no-go on AI, with the evidence to justify it. If at least one model clears the product's ≥99% route-accuracy bar across the corpus, we adopt AI for v1 and pick that model. If no model clears it, the spike's output is the input to a follow-up algorithmic spike.

---

## 2. Functional Requirements (The "What")

### 2.1 Run the corpus against a chosen AI model

- **As a** decision-maker, **I want to** run the entire scenario corpus against any one of Opus, Sonnet, or Haiku, **so that** I can compare them on the same evidence.
  - **Acceptance Criteria:**
    - [x] A single command runs the full corpus against a selected model and produces a report file.
    - [x] The same command accepts a subset (e.g., one scenario or one shape) for fast iteration.
    - [x] Re-running with the same model and corpus produces results that can be compared run-over-run (timestamped, not overwritten silently).

### 2.2 Append, don't rebuild (hard gate)

- **As a** decision-maker, **I want** every scenario to be evaluated by feeding fragments **one at a time**, carrying the prior route forward, **so that** I can verify the AI engine never destroys and re-creates route-cities that already existed.
  - **Acceptance Criteria:**
    - [x] For every scenario, fragments are delivered to the engine sequentially in the corpus's defined order (forward / reverse / bisect / seeded-shuffle).
    - [x] After each fragment, the engine returns an updated route built **on top of** the prior one — never from scratch.
    - [x] A route-city present in step N must still be the **same** route-city in step N+1 unless the new fragment legitimately replaces it (e.g., user edits scope, out of spike).
    - [x] A scenario is marked **passed** only if BOTH (a) the final route matches the expected route AND (b) no route-city identity was destroyed and re-created at any step during the replay. Either failure fails the scenario.

### 2.3 Handle routes with gaps

- **As a** decision-maker, **I want** the engine to keep the city sequence correctly ordered even when some legs between cities are missing, **so that** users who upload documents piecemeal (or leave gaps on purpose) still see their trip in the right order.
  - **Acceptance Criteria:**
    - [x] During incremental replay, the partial route after each intermediate step has its cities in the correct order relative to the final expected route, even when the connecting fragment for one or more legs has not yet arrived.
    - [x] The engine does not invent, drop, or reorder cities to "close" a gap; gaps are simply the natural state of a partial route. (No explicit "gap marker" is required from the engine in this spike.)

### 2.4 Comparison report

- **As a** decision-maker, **I want** a report comparing all three models on the same corpus, **so that** I can pick one.
  - **Acceptance Criteria:**
    - [x] The report shows, per model: route accuracy (% of scenarios passed under the hard gate in 2.2), wall-clock latency per scenario, and estimated cost per scenario and per full corpus run.
    - [x] The report includes a per-axis breakdown of accuracy (shape, traveler count, fragment ordering, return, hotels) so weak spots are visible.
    - [x] Failures are summarized as **counts only** in the report; no per-scenario diffs are required up front (drilling in is a manual follow-up).
    - [x] The report ends with a clear recommendation: which model (if any) clears the ≥99% accuracy bar, and the suggested v1 choice — or "no AI model qualifies, proceed to algorithmic spike."

### 2.5 Reference implementation

- **As a** decision-maker, **I want** the spike to leave behind a thin reference implementation shaped like the eventual backend engine, **so that** the cost and shape of integration are visible at decision time, not discovered later.
  - **Acceptance Criteria:**
    - [x] The implementation exposes the same conceptual entrypoint the production engine would: "given an existing route and one new document fragment, return an updated route."
    - [x] The runner that produces the report uses this same entrypoint — there is no separate "evaluation-only" code path.
    - [x] The implementation is readable and small enough to be lifted, with refinement, into the v1 backend.

---

## 3. Scope and Boundaries

### In-Scope

- LLM-driven route engine prototype, tested against the existing 192-scenario corpus.
- Three Claude models on Bedrock: Opus, Sonnet, Haiku.
- Incremental (one-fragment-at-a-time) replay across all scenarios for every model.
- Comparison report covering accuracy, per-axis breakdown, latency, cost, and a recommendation.
- A reusable corpus runner.
- A thin reference engine implementation shaped like the future production engine.
- Hard-gate verification of append/identity-preservation behavior.
- Verification of correct city ordering in the presence of gaps.

### Out-of-Scope

- The algorithmic (non-AI) engine approach. It is a separate follow-up spike, triggered only if no AI model clears the accuracy bar.
- PDF extraction. The spike consumes pre-structured fragments from the corpus; extracting fragments from real PDFs is the **Document Ingest** umbrella.
- The Trip Route View UI. The spike produces a route data structure, not a screen.
- Custom legs (custom accommodations and custom transportation) — Phase 2.
- Completeness checks ("missing transit/accommodation per traveler") — Phase 2.
- Explicit "gap markers" or any UI/UX for surfacing gaps. The engine only needs to keep the order correct around gaps; how the app *displays* a gap is a later UI decision.
- Per-scenario failure diffs in the report (counts only).
- Non-Claude models, self-hosted models, or any non-Bedrock provider.
- Producing the final production engine — this spike's reference implementation is a starting point, not the shipped engine.
- The **Mock-Document Corpus** roadmap item (corpus already exists from project bootstrap; this spike consumes it).
- The **Trip Route View** roadmap item (separate spec, depends on the engine chosen here).
- All Phase 2 and Phase 3 roadmap items.
