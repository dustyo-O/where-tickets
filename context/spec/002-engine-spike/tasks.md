# Tasks: Engine Spike — LLM-Driven Route Updater

> Each slice is end-to-end runnable. Don't move on until the slice's verification passes.

---

- [x] **Slice 1: Route model + deterministic operation applier (offline)**
  - [x] Create `backend/spikes/route_engine_llm/` package; add `models.py` with `Accommodation`, `RouteStop`, `Transit`, `WorkingRoute` (engine-owned monotonic `stop-N` IDs) and the `Fragment` transit/hotel union mirroring `corpus/schema/extracted-fragment.schema.json`. **[Agent: python-backend]**
  - [x] Add `operations.py`: Pydantic op models (`create_stop`, `enrich_stop`, `add_transit`, `attach_accommodation`, `add_travelers`) and the deterministic `apply(route, ops) -> route` applier that mints fresh IDs, validates referenced `stopId`s, and rejects dangling/conflicting ops. **[Agent: python-backend]**
  - [x] Add `backend/tests/spikes/test_applier.py`: create/enrich/transit/accommodation cases plus the **circle double-`ROM`** case (enrich the right stop, never merge the two) and a dangling-ID rejection case. **[Agent: python-backend]**
  - [x] **Verify:** `cd backend && uv run pytest tests/spikes/test_applier.py` passes; applier never reassigns an existing ID. **[Agent: python-backend]**

- [ ] **Slice 2: Corpus loader + scoring (offline)**
  - [ ] Add `corpus.py`: load a scenario's ordered `fragments/*.json` + `expected-route.json` from `corpus/scenarios/`, parsing into the Slice 1 models. **[Agent: python-backend]**
  - [ ] Add `scoring.py`: (a) **final-route match** — strip engine IDs, normalize, compare `stops` sequence + `transits` set field-by-field vs expected; (b) **identity preservation** — stop-ID set append-only, city stable per ID across snapshots; (c) **gap/ordering** — partial stops are an order-preserving subsequence of final positions by identity. A scenario passes only if all three hold. **[Agent: python-backend]**
  - [ ] Add `backend/tests/spikes/test_scoring.py`: feed an expected route back through the matcher (self-match = pass); crafted snapshot sequences that trip each of the three checks. **[Agent: python-backend]**
  - [ ] **Verify:** `uv run pytest tests/spikes/test_scoring.py` passes; a deliberately reordered/identity-broken snapshot fails the right check. **[Agent: python-backend]**

- [ ] **Slice 3: Engine + Bedrock wiring, proven offline (stubbed)**
  - [ ] Add `anthropic[bedrock]` as a new `spike` dependency group in `backend/pyproject.toml`; add `backend/spikes/route_engine_llm/runs/` to `.gitignore`. **[Agent: python-backend]**
  - [ ] Add `prompts.py` (system prompt + single operation-list tool schema, marked for prompt caching) and `bedrock_client.py` (thin `AnthropicBedrock` wrapper: model alias→inference-profile-ID map, `tool_choice` forced, `temperature=0`, token-usage + per-call latency capture, bounded retry/backoff). **[Agent: bedrock-llm]**
  - [ ] Add `engine.py`: `update_route(route, fragment)` — render prompt, call client, parse tool-use into op models, `apply`. **[Agent: bedrock-llm]**
  - [ ] Add `backend/tests/spikes/test_engine_contract.py`: drive `update_route` end-to-end against a **recorded/stubbed** Bedrock response (no network) and assert the route mutates correctly. **[Agent: bedrock-llm]**
  - [ ] **Verify:** `uv run pytest tests/spikes/` passes with no AWS credentials present (CI-safe). **[Agent: python-backend]**

- [ ] **Slice 4: Runner + per-run report, live on a subset**
  - [ ] Add `pricing.py` (dated per-model USD table; cost from token usage) and `report.py` (per-run `results.json` + `report.md`: accuracy, per-axis breakdown, latency p50/p95/mean, cost; failures bucketed by which check failed — counts only). **[Agent: bedrock-llm]**
  - [ ] Add `run.py` CLI (`python -m spikes.route_engine_llm.run --model {opus|sonnet|haiku} [--scenario/--shape/--limit]`): empty route → feed fragments in corpus order → `update_route` per fragment → snapshot → score; write to `runs/<timestamp>-<model>/`. Add a `just spike-engine` recipe wrapping it. **[Agent: bedrock-llm]**
  - [ ] **Verify (live, needs AWS):** `just spike-engine model=haiku --limit 3` completes against real Bedrock, writes `results.json` + `report.md`, and token/cost/latency are populated. **[Agent: bedrock-llm]**

- [ ] **Slice 5: Full sweep + cross-model comparison & recommendation**
  - [ ] Extend `report.py` with `compare.md` generation: read multiple run JSONs and tabulate Opus vs Sonnet vs Haiku (accuracy, per-axis, latency, cost) with a go/no-go recommendation against the ≥99% bar. **[Agent: bedrock-llm]**
  - [ ] Run the full 192-scenario corpus for each of the three models; generate the comparison. **[Agent: bedrock-llm]**
  - [ ] **Verify (live, needs AWS):** three full run dirs exist; `compare.md` renders all three models side-by-side and states whether any clears ≥99% — the evidence for the DUS-21 ADR. **[Agent: bedrock-llm]**

---

## Prerequisites

| Task/Slice | Issue | Recommendation |
| --- | --- | --- |
| Slices 4 & 5 verification | Live Bedrock calls need AWS credentials with **Bedrock model access enabled for Opus, Sonnet, and Haiku** in your `AWS_REGION` | Confirm creds + model access before running; Slices 1–3 need nothing and run in CI |
