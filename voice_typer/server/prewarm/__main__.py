# ARCH-045 / SPLIT-4: extracted from the original ``prewarm.py`` god-module.
"""Entry point for ``python -m voice_typer.server.prewarm``.

Delegates to :func:`voice_typer.server.prewarm.main` (defined in
:mod:`.cli`).  Kept as a one-liner so the package's ``__init__.py`` can
re-export ``main`` without also binding ``__name__ == "__main__"``
side-effects at import time.
"""

import sys

from voice_typer.server.prewarm.cli import main

if __name__ == "__main__":
    sys.exit(main())
