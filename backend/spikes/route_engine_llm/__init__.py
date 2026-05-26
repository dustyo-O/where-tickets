"""Engine Spike — LLM-driven route updater (Slice 1: deterministic core).

This package is an isolated, in-memory spike. It has no DB, FastAPI, or AWS
dependencies. Slice 1 provides the engine-owned route model (`models.py`) and
the deterministic operation applier (`operations.py`) that later slices drive
with LLM-produced operations.
"""
