"""Algorithmic (rules-based) sibling of the LLM route-engine spike.

Deterministic counterpart to :mod:`spikes.route_engine_llm`: same conceptual
entrypoint (``update_route(route, fragment) -> UpdateResult``), but folded by
hand-written rules instead of a Bedrock call. Slice 1 is the minimum runnable
increment — only single-leg transit tickets on an empty route are handled; every
other fragment shape is bucketed as an ``engine_error`` so the sweep continues.
"""
