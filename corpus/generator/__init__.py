"""Deterministic generator of route-assembly scenarios.

Public entry point: ``python -m corpus.generator``.

The generator emits one folder per scenario into ``corpus/scenarios/`` with:
- ``fragments/NN-<doc-type>.json`` — inputs in the order dictated by the axis
- ``expected-route.json`` — the canonical composed answer
- ``README.md`` — one-line summary

All output is byte-identical across runs: fixed epoch, seeded RNG, sorted keys.
"""

from .matrix import build_matrix
from .scenario import generate_scenario

__all__ = ["build_matrix", "generate_scenario"]
