# Technical Specification: Algorithmic Engine Spike

- **Functional Specification:** `context/spec/003-algorithmic-engine-spike/functional-spec.md`
- **Linear:** [DUS-20](https://linear.app/dusty-work/issue/DUS-20/spike-b-algorithmic-graph-route-builder)
- **Status:** Draft
- **Author(s):** Alexander Shleyko

---

## 1. High-Level Technical Approach

A self-contained sibling package under `backend/spikes/route_engine_algorithmic/` that exposes the same conceptual entrypoint as the LLM spike — `update_route(route, fragment) -> UpdateResult` — but implemented deterministically with rules. No LLM, no Bedrock, no AWS, no spike dependency group. Pure stdlib + Pydantic.

It **reuses the LLM spike's harness verbatim** by direct import: `models` (`WorkingRoute`, `Fragment`, `RouteStop`, `Transit`, `Accommodation`), `operations` (op union + the identity-preserving `apply()` and engine-derived stop projection), `corpus.load_scenario`, `scoring.score_scenario`, `report.build_results_json`/`build_report_md`, `compare.build_compare_md`. The runner mirrors `route_engine_llm/run.py` but calls the algorithmic `update_route` instead of the Bedrock-backed one. **The algorithmic engine writes a `results.json` with the exact same schema, under the engine alias `algorithmic`**, so `just spike-compare` consumes it alongside Opus/Sonnet/Haiku runs and produces one unified comparison for the DUS-21 ADR.

The naming is intentionally asymmetric — `route_engine_llm` already exists and is committed; extracting a `engine_core` shared package is meaningful refactoring that's better done when one engine is promoted to production (per the spike's "small enough to be lifted with refinement" deliverable). The TODO is documented on the imports.

The interesting work is the engine itself: a per-fragment decision pipeline that mirrors the prompt's "per-traveler per-slot identity" rules but as code — for each city the fragment mentions, decide CREATE-new vs ENRICH-existing using exactly the three explicit conditions, then emit the corresponding op list and route it through the existing `operations.apply()` so identity preservation and stop projection come for free.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Module layout

| Path | Responsibility |
|------|---------------|
| `backend/spikes/route_engine_algorithmic/__init__.py` | Package marker |
| `.../engine.py` | `update_route(route, fragment) -> UpdateResult`. Builds an op list deterministically, applies via the shared `operations.apply` (which projects stop fields), records wall-clock latency. No usage/cost. |
| `.../rules.py` | Pure decision functions, no I/O — the per-city CREATE-vs-ENRICH classifier, transit/hotel wiring, chronological positioning. Heavily unit-testable. |
| `.../run.py` | CLI: `python -m spikes.route_engine_algorithmic.run [--scenario … | --shape … | --limit N]`. No `--model`; the engine alias is hardcoded `algorithmic`. Writes `runs/<timestamp>-algorithmic/{results.json,report.md}`. |
| `backend/tests/spikes/test_algorithmic_engine.py` | Unit tests for `rules.py` (every condition + every fragment shape) and end-to-end tests for `engine.update_route` against crafted scenarios. |
| `backend/tests/spikes/test_algorithmic_corpus.py` | Smoke: run the engine over a few canonical corpus scenarios (one straight, one circle, one star) and assert pass — guards against regressions during rule iteration. |

`pricing.py` is **not used** (cost = 0). `bedrock_client.py`, `prompts.py` are LLM-specific and not imported.

Shared imports come from `spikes.route_engine_llm`:

```
from spikes.route_engine_llm.models import Fragment, WorkingRoute, ...
from spikes.route_engine_llm.operations import (
    Op, CreateStop, AddTransit, AttachAccommodation, EnrichStop, AddTravelers, apply,
)
from spikes.route_engine_llm.corpus import load_scenario, list_scenarios
from spikes.route_engine_llm.scoring import score_scenario
from spikes.route_engine_llm import report
```

A module-level comment on these imports notes that the shared types will move to a common `engine_core` package when one engine is promoted to production. The `UpdateResult` dataclass (currently defined in `route_engine_llm.engine`) is either re-imported or redefined here — leaning re-import to keep the shape canonical.

### 2.2 The engine contract

`update_route(route: WorkingRoute, fragment: Fragment) -> UpdateResult`

- Takes the current route (mutated by prior fragments in the replay) + one new fragment.
- Returns an `UpdateResult` with the mutated route, the parsed `ops` list (for debugging / future inspection), zero `usage`, and the wall-clock `latency_seconds`.
- Raises `EngineError` on any rules-pipeline failure (analogous to the LLM `EngineError` — fragment-level bucketed failure rather than a process crash). Same dangling-id `OpApplyError` semantics inherited from `operations.apply`.

### 2.3 The decision pipeline (per fragment)

A single function `rules.build_ops(route, fragment) -> list[Op]` orchestrates three concerns:

1. **Identify the city events implied by the fragment.**
   - Transit ticket: each leg contributes a `(from-city, departure-at, travelers)` and a `(to-city, arrival-at, travelers)` event.
   - Hotel booking: one `(city, check-in, check-out, travelers)` event.
   - Output: an ordered list of "events" annotated with their role (departure, arrival, accommodation).

2. **Classify each city event** — CREATE-new vs ENRICH-existing — using the three explicit conditions, in this order, against the current route's stops and the operations being built so far in this same batch (so a new stop created earlier in the batch is considered "in the route" for subsequent events):
   - (a) city not in route → CREATE
   - (b) the existing same-city stop's timing is chronologically disjoint from this event AND at least one different-city stop sits between them in time (using projected stop arrival/departure times) → CREATE
   - (c) the existing same-city stop already has this event's slot filled for this traveler (e.g. arrival-at already set with this traveler) → CREATE
   - else → ENRICH (target the existing same-city stop's id)
   - Plus the sanity check: if ENRICH would produce `arrival > departure` on the target stop, it's actually a CREATE.

3. **Emit ops in the order the applier expects.** For each new stop, emit `create_stop` with a `ref` and an `after` resolved against the chronological neighbor (an existing stop id or an earlier batch ref). Then `add_transit`s (referencing the right ids/refs at each endpoint), `attach_accommodation`s, and `add_travelers`/`enrich_stop` overrides for the rare no-transit cases. The shared `operations.apply` handles identity (engine-owned monotonic ids), gap-safe insertion (`after`), and stop projection from transits — algorithmic doesn't reimplement any of that.

This split means `rules.py` is small, table-driven, and exhaustively unit-testable; `engine.py` is essentially `apply(route, rules.build_ops(route, fragment))` plus latency capture and error wrapping.

### 2.4 Chronological reasoning

The CREATE-vs-ENRICH classifier needs a chronological view of the existing route. The `WorkingRoute` already projects each stop's `arrivalAt`/`departureAt` from transits (via `operations.apply`'s stop projection), so the algorithm can sort stops by their projected `arrivalAt` (fallback `departureAt`) to derive the time-ordered position used by condition (b). No new data model needed.

For the "intervening different-city stop in time" part of (b), the check is: does any stop with `city != event.city` have a projected time strictly between the existing same-city stop's time and the event's time. Pure list scan — fast at this scale.

### 2.5 Runner & report integration

`run.py` mirrors `route_engine_llm/run.py`'s structure:

- `argparse` with the same filter flags (`--scenario`, `--shape`, `--limit`), no `--model`/`--region`.
- For each scenario: empty `WorkingRoute`, replay fragments in corpus-defined order via `engine.update_route`, snapshot per step (deep-copy), then `scoring.score_scenario(snapshots, expected)` (identical hard gates).
- Per-scenario record uses the same schema fields. `usage` zero, `costUsd` zero, latency real, axes parsed via the existing `report.parse_axes`.
- Calls `report.build_results_json(...)` and `report.build_report_md(...)` with `model="algorithmic"` and `modelId="algorithmic"` (or similar — see §3 risks). Writes to `runs/<timestamp>-algorithmic/`. Same path scheme as LLM.
- `compare.build_compare_md` already takes N run payloads with no LLM assumption — it should accept this run as-is and render it as another column.

### 2.6 `justfile`

A new recipe alongside `spike-engine` / `spike-compare`:

```
spike-engine-algo *args:
    cd backend && uv run python -m spikes.route_engine_algorithmic.run {{args}}
```

No `--group spike` needed (no `anthropic` dependency).

---

## 3. Impact and Risk Analysis

- **System dependencies:** none in production. Spike is isolated under `backend/spikes/route_engine_algorithmic/`. Depends only on the committed corpus and the shared types in `spikes.route_engine_llm`. No AWS, no DB, no network, no extra Python deps.
- **Risks & mitigations:**
  - **Rule sprawl** — a deterministic engine tends to grow exceptions. Mitigation: keep `rules.py` to the three explicit identity conditions + the sanity check + the small set of event-shape converters; resist adding scenario-specific patches. Each new rule needs a passing failing test BEFORE the rule, and rules ordered by precedence with that order documented.
  - **Overfitting to the corpus generator's shapes** — the corpus is generated by a known generator with three shapes; rules could implicitly target those. Mitigation called out in the functional spec (out of scope to extend the corpus); the rules are written against the *expected-route schema invariants*, not against shape names, so any rule that branches on "if straight…" is a smell to reject in code review.
  - **Compare.md schema compatibility** — `report.build_results_json` may have LLM-specific assumptions (model id, usage structure). Mitigation: pass `model="algorithmic"` and zeroed usage; if any field is required, supply a neutral default and (one) tiny tweak to `report.py` rather than forking the schema. The existing `compare.py` is model-agnostic by construction (renders whatever models it's given).
  - **Determinism in latency reporting** — wall-clock latency is non-deterministic by nature. Acceptance criterion §2.6 ("byte-identical re-runs") allows the *timestamp* and *per-scenario latency* to vary; everything else (pass/fail, ops emitted, route shape) must be identical. Document the carve-out clearly in `results.json` (or assert identicalness of everything but `latency` / `startedAt` in the determinism test).
  - **Sibling-import naming friction** — importing from `route_engine_llm` for non-LLM types is awkward. Mitigation: module-level comment + TODO; the actual refactor lands when one engine is promoted (out of spike scope per the functional spec).

---

## 4. Testing Strategy

- **Unit (offline, CI-safe):**
  - `rules.py` — exhaustive per-condition tests: (a) empty route + new city, (b) chronological-disjoint either direction with and without intervening different-city stops, (c) per-traveler slot filled, plus the arrival-after-departure sanity check. Each condition has a test that constructs a small `WorkingRoute` and asserts the expected op shape.
  - `engine.update_route` — end-to-end with crafted fragments: single-leg empty-route, multi-leg empty-route, circle (single revisit), star (multiple hub revisits), reverse-arriving outbound ticket on a pre-existing closing leg (the trap that defeated Sonnet at 79.2%), hotel-only stop.
- **Corpus smoke** — run the engine over `000-straight-1p-forward`, `064-circle-1p-forward`, `128-star-1p-forward` and assert `score_scenario` passes for each. Cheap, catches regressions during rule iteration.
- **Full corpus run** — `just spike-engine-algo` over all 192. This is the production deliverable for the spike; should be runnable in CI (no creds, no spend) so accuracy is tracked over time, not just at decision time.
- **Determinism test** — run the full corpus twice in a row, diff the two `results.json` files ignoring `startedAt` and per-scenario `latency*` fields, assert empty diff.
- The LLM spike's 101 offline tests stay untouched and still pass (no changes to `route_engine_llm/*`).
