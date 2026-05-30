# Functional Specification: Algorithmic Engine Spike

- **Roadmap Item:** Route Engine (Foundation) → Engine Spike & Decision (algorithmic half)
- **Linear:** [DUS-20](https://linear.app/dusty-work/issue/DUS-20/spike-b-algorithmic-graph-route-builder)
- **Status:** Completed
- **Author:** Alexander Shleyko

---

## 1. Overview and Rationale (The "Why")

The LLM half of the route-engine spike (DUS-19) landed with a clear negative: even Opus 4.6 tops out at 83.9% on the 192-scenario corpus — ~15 points short of the ≥99% bar — and the failures concentrate on **star routes** (Opus 59.4%) and **reverse fragment ordering** (Opus 72.9%). Those are exactly the kind of structured, identity-heavy graph problems rules can excel at: per-traveler-per-slot identity, chronological reasoning, hub-and-spoke shapes.

This spike asks the symmetric question: **can a deterministic, rules-based engine clear the ≥99% bar where the LLM could not?** If yes, algorithmic is the v1 engine. If not, the DUS-21 ADR has the full evidence side-by-side (Opus 83.9% vs whatever-algorithmic-scores) and a real basis to consider next moves — further LLM prompt iteration, an algorithmic + LLM hybrid, or revisiting the bar with product.

**Success of the spike** is producing the algorithmic engine's numbers on the *exact same* corpus, scored by the *exact same* hard gates, formatted to plug directly into the existing cross-engine comparison report alongside the LLM numbers.

---

## 2. Functional Requirements (The "What")

### 2.1 Run the corpus against the algorithmic engine

- **As a** decision-maker, **I want to** run the entire scenario corpus through the algorithmic engine, **so that** I get an apples-to-apples accuracy number against the LLM models.
  - **Acceptance Criteria:**
    - [x] A single command runs the full corpus against the algorithmic engine and produces a report file.
    - [x] The same command accepts a subset (e.g., one scenario or one shape) for fast iteration during rules refinement.
    - [x] Re-running with the same engine and corpus produces results that can be compared run-over-run (timestamped, never overwritten silently).

### 2.2 Append, don't rebuild (hard gate)

- **As a** decision-maker, **I want** every scenario to be evaluated by feeding fragments **one at a time**, carrying the prior route forward, **so that** the algorithmic engine is held to the same identity invariant as the LLM engine was.
  - **Acceptance Criteria:**
    - [x] For every scenario, fragments are delivered to the engine sequentially in the corpus's defined order (forward / reverse / bisect / seeded-shuffle).
    - [x] After each fragment, the engine returns an updated route built **on top of** the prior one — never from scratch.
    - [x] A route-city present in step N must still be the **same** route-city in step N+1 unless the new fragment legitimately replaces it (out of spike scope).
    - [x] A scenario is marked **passed** only if BOTH (a) the final route matches the expected route AND (b) no route-city identity was destroyed and re-created at any step during the replay. Either failure fails the scenario.

### 2.3 Handle routes with gaps

- **As a** decision-maker, **I want** the engine to keep the city sequence correctly ordered even when some legs between cities are missing, **so that** the gap-tolerance guarantee is preserved.
  - **Acceptance Criteria:**
    - [x] At every intermediate step, the partial route's cities are in an order consistent with their final positions, even when the connecting fragment for one or more legs hasn't arrived.
    - [x] The engine does not invent, drop, or reorder cities to "close" a gap; gaps are simply the natural state of a partial route.

### 2.4 Comparison-ready report

- **As a** decision-maker, **I want** the algorithmic spike's output to slot directly into the existing cross-engine comparison so the DUS-21 ADR renders **all** engines (Haiku, Sonnet, Opus, algorithmic) in one table.
  - **Acceptance Criteria:**
    - [x] The spike's run produces a `results.json` with the **same schema** as the LLM spike's `results.json`, under an engine alias like `algorithmic`, so `just spike-compare <llm runs…> <algorithmic run>` renders all engines side-by-side.
    - [x] The per-run `report.md` covers route accuracy (% scenarios passed under the all-three-checks gate), per-axis breakdown (shape, traveler count, fragment ordering, return, hotels), and per-scenario wall-clock latency.
    - [x] Failures are summarized as **counts only**, bucketed by which check failed.
    - [x] Per-scenario / per-run cost is reported as **$0 / negligible** (no inference spend) — the cost difference vs the LLM engines is visible in the comparison.
    - [x] The cross-engine `compare.md` lists the algorithmic engine alongside Opus/Sonnet/Haiku with an explicit go/no-go against the ≥99% bar.

### 2.5 Reference implementation

- **As a** decision-maker, **I want** the spike to leave behind a thin reference implementation shaped like the eventual backend engine, **so that** the cost and shape of integration are visible at decision time.
  - **Acceptance Criteria:**
    - [x] The implementation exposes the **same conceptual entrypoint** as the LLM spike: "given an existing route and one new document fragment, return an updated route."
    - [x] The runner uses the **same eval harness** as the LLM spike (corpus loader, scoring, reporter) — there is no separate evaluation-only code path.
    - [x] The implementation is readable and small enough to be lifted, with refinement, into the v1 backend.

### 2.6 Determinism

- **As a** decision-maker, **I want** re-running the algorithmic engine on the same corpus to produce identical results, **so that** accuracy numbers are reproducible without noise from stochastic models.
  - **Acceptance Criteria:**
    - [x] Two back-to-back full runs produce byte-identical `results.json` (modulo the run timestamp itself).
    - [x] No reliance on randomness, wall-clock-dependent inputs, or external services.
    - [x] CI-runnable (no AWS, no Bedrock, no spend).

---

## 3. Scope and Boundaries

### In-Scope

- Deterministic, rules-based route engine prototype, tested against the existing 192-scenario corpus.
- Reuse of the existing eval harness (`corpus.py`, `scoring.py`, the runner shape, `report.py` schema, `compare.py`) from the LLM spike.
- Hard-gate verification of append/identity-preservation behavior, identical to the LLM spike.
- Verification of correct city ordering in the presence of gaps, identical to the LLM spike.
- Output that plugs directly into the existing cross-engine `compare.md`.
- A thin reference engine implementation shaped like the future production engine.

### Out-of-Scope

- The LLM spike (DUS-19) — done; this is its deterministic counterpart.
- The DUS-21 ADR itself — separate ticket; runs after this spike produces numbers.
- PDF extraction (Document Ingest umbrella).
- The Trip Route View UI.
- Custom legs and completeness checks (Phase 2 roadmap).
- "Wild" out-of-generator-coverage corpus scenarios — both spikes evaluate on the same agreed 192-scenario corpus for a fair head-to-head; extending the corpus is a separate item.
- Hybrid (algorithmic + LLM) or LLM-reviewer designs — possible follow-ups if neither pure approach clears the bar; explicitly out-of-scope for this spec.
- Producing the final production engine — this spike's reference implementation is a starting point, not the shipped engine.
- All Phase 2 and Phase 3 roadmap items.
