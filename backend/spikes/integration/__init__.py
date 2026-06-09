"""Integration runner: live extractor -> engine fragments -> working route.

DUS-31 Slice 6 lands the pure-mapping :func:`extracted_fields_to_fragment`
adapter (no I/O, no Bedrock, no PDF parsing). DUS-31 Slice 7 lands the
``runner.py`` CLI that drives extract -> adapt -> engine -> scoring end to
end against the trip directories under ``corpus/integration/``, plus
``report.py`` for the human-readable + JSON rendering.
"""
