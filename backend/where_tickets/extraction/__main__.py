"""Entry point for ``python -m where_tickets.extraction``.

Pure delegation to :func:`where_tickets.extraction.cli.main` — see that
module for the CLI contract (argv, stdout/stderr split, exit codes).
"""

from __future__ import annotations

import sys

from where_tickets.extraction.cli import main

if __name__ == "__main__":
    sys.exit(main())
