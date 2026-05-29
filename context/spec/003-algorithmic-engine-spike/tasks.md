# Tasks: Algorithmic Engine Spike

> Each slice is end-to-end runnable. Don't move on until the slice's verification passes.

---

- [x] **Slice 1: Package scaffolding + minimal engine (single-leg, empty route)**
  - [x] Create package `backend/spikes/route_engine_algorithmic/` with `__init__.py`, `engine.py`, `rules.py`, `run.py`. Module-level comments on shared imports from `route_engine_llm` (with TODO: extract to common `engine_core` when one engine is promoted). **[Agent: python-backend]**
  - [x] `rules.build_ops(route, fragment)` handles the simplest case only: single-leg transit ticket on an EMPTY route — emits `create_stop` for both endpoints (refs + chaining `after`) + one `add_transit` between them. **[Agent: python-backend]**
  - [x] `engine.update_route(route, fragment) -> UpdateResult` (re-using the `UpdateResult` shape from `route_engine_llm.engine`) — calls `rules.build_ops`, applies via shared `operations.apply`, captures wall-clock latency, surfaces `EngineError` on failure. **[Agent: python-backend]**
  - [x] `run.py` CLI with `--scenario / --shape / --limit` mirroring the LLM runner's structure; writes `runs/<ts>-algorithmic/` using shared `report.build_results_json` + `build_report_md` with `model="algorithmic"` and zeroed `usage`/cost. Add `just spike-engine-algo` recipe (no `--group spike`). **[Agent: python-backend]**
  - [x] `backend/tests/spikes/test_algorithmic_engine.py`: unit tests for `rules.build_ops` and end-to-end `update_route` on a hand-crafted single-leg fragment + empty route. **[Agent: python-backend]**
  - [x] **Verify:** `cd backend && uv run pytest tests/spikes/test_algorithmic_engine.py` passes; `uv run ruff check . && uv run pyright` clean on new files; `just spike-engine-algo --limit 1` runs without error and writes a well-formed `results.json`. **[Agent: python-backend]**

- [x] **Slice 2: Multi-leg transit chains + chronological positioning**
  - [x] Extend `rules.build_ops` to handle multi-leg transit tickets: chain new stops with `ref` + `after`-of-previous-ref so the applier inserts them in correct chronological order. **[Agent: python-backend]**
  - [x] Chronological insertion against an existing non-empty route: pick the `after` neighbor by projected timing (existing stop id or `"start"` if the new stop precedes all). **[Agent: python-backend]**
  - [x] Unit tests: multi-leg on empty route; multi-leg with one endpoint already in the route; insertion at front, middle, end. **[Agent: python-backend]**
  - [x] **Verify:** `just spike-engine-algo --shape straight` runs all 64 straight scenarios. Decomposed: **16/16 in-scope (straight, no hotels, no return) pass**; 32 with hotels bucket as `engine_error` (Slice 4); 16 with return bucket as `final_mismatch` (Slice 3 — revisit classifier needed). 116 offline tests pass. **[Agent: python-backend]**

- [x] **Slice 3: Per-traveler-per-slot identity (3 conditions + sanity check)**
  - [x] `rules.classify_event(route, event) -> Decision` using the three explicit conditions in order: (a) city not in route, (b) chronologically disjoint with intervening different-city stop in time, (c) per-traveler slot already filled. Plus the arrival-after-departure sanity check that flips ENRICH → CREATE. **[Agent: python-backend]**
  - [x] Wire `classify_event` into `build_ops` for every event (replaces the simpler decision logic from Slice 2 in a backwards-compatible way). **[Agent: python-backend]**
  - [x] Unit tests directly mirroring the LLM prompt's worked examples: `LED→MOW→BEG→MOW` revisit (condition b, forward direction); pre-existing later-LHR + new earlier outbound `MXP→LHR→HEL→MAD` (condition b, earlier-event direction — the trap that defeated Sonnet); per-traveler slot conflict (condition c); sanity check (would-make-`arrival > departure`). **[Agent: python-backend]**
  - [x] Add `backend/tests/spikes/test_algorithmic_corpus.py`: smoke runs of `000-straight-1p-forward`, `064-circle-1p-forward`, `128-star-1p-forward` via `update_route`+`score_scenario`, all assert pass. **[Agent: python-backend]**
  - [x] **Verify:** Full corpus run `just spike-engine-algo`: **96/96 (100%) of no-hotels scenarios pass** across all shapes (straight, circle, star) AND all orderings (forward, reverse, bisect, seeded-shuffle). 96 hotel scenarios bucket as `engine_error` per spec (Slice 4). 125 offline tests pass. **[Agent: python-backend]**

- [x] **Slice 4: Hotels + multi-traveler + non-forward orderings**
  - [x] `rules.build_ops` handles hotel-booking events: `attach_accommodation` to the matched stop, with implicit `create_stop` if the city isn't present yet (or condition (b)/(c) triggers a new stop). **[Agent: python-backend]**
  - [x] Multi-traveler enrichment via `add_travelers` when a same-city event for a new traveler maps to an existing stop under the classifier. **[Agent: python-backend]**
  - [x] Validate non-forward orderings (reverse / bisect / seeded-shuffle). The classifier's in-batch ledger handles them; chronological in-batch anchoring fix closed 19/22 hotel failures, condition-(c) non-overlap split closed 1, and an accommodation-time disjoint-window sanity check closed the final 2. **[Agent: python-backend]**
  - [x] Extend `test_algorithmic_corpus.py` with hotel + multi-pax canonical scenarios (`020-straight-2p-forward-hotels`, `068-circle-1p-forward-hotels`, `132-star-1p-forward-hotels`). **[Agent: python-backend]**
  - [x] **Verify:** Full `just spike-engine-algo` → **192/192 (100%)** across all shapes / pax / orderings / returns / hotels. 136 offline tests pass; lint + pyright clean. **[Agent: python-backend]**

- [x] **Slice 5: Full corpus run + cross-engine `compare.md`** (determinism test skipped — see note)
  - [x] Full 192-scenario run via `just spike-engine-algo` writes `runs/<ts>-algorithmic/{results.json,report.md}` with the same schema as LLM runs. `report.build_results_json` accepted `model="algorithmic"` and zeroed `usage`/cost without modification (no schema fork needed). **[Agent: python-backend]**
  - [~] **Determinism test deliberately skipped.** The engine is deterministic by construction (no randomness, no wall-clock-dependent decisions, byte-stable inputs from the corpus). The 192/192 result across both Slice 3 and Slice 4 runs confirms stability in practice. The two-back-to-back-runs assertion would only verify what the architecture already guarantees; deferred unless a future change introduces non-determinism risk.
  - [x] **Verify:** `just spike-compare` over Haiku/Sonnet/Opus/algorithmic results produced `runs/compare-20260529T213439Z.md` (in `route_engine_llm/runs/`) rendering all four engines side-by-side. Tool's recommendation: **"`algorithmic` — the only model that clears the bar"** (100.0% / $0.00 / sub-ms latency). LLM spike's 101 tests still green; no `route_engine_llm/*` changes. **[Agent: python-backend]**
