"""PDF extraction package — public surface for spec 006 (AI Document Understanding).

Re-exports the three symbols the corpus runner and SQS pipeline depend on:
:func:`extract_pdf` (the entry point), :class:`ExtractedFields` (the result
shape), and :class:`ExtractionFailedError` (the all-paths-failed signal).
"""

from where_tickets.extraction.extract import (
    ExtractedFields,
    ExtractionFailedError,
    extract_pdf,
)

__all__ = ["ExtractedFields", "ExtractionFailedError", "extract_pdf"]
