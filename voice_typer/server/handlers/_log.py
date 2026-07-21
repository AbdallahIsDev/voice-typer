"""Shared logger for the IPC handler mixin package (CR-63).

ARCH-REFAC-002 / CR-63: prior to this module, each of the 13 handler
mixins under ``voice_typer/server/handlers/`` opened its own copy of::

    log = logging.getLogger("voice_typer.server.ipc_server")

That is 14 copies of the same one-liner (the package ``__init__.py``
also documented the pattern). The duplication was harmless at runtime
(``logging.getLogger`` returns the same logger object for a given name)
but it made the mixins harder to audit: a reader had to verify that
every copy used the *same* logger name, and a future rename (e.g.
splitting ``ipc_server`` from ``ipc.server``) would have required
touching 14 files.

This module is the single source of truth for the handler-package
logger name. Mixins import ``log`` from here::

    from voice_typer.server.handlers._log import log

The logger name stays ``"voice_typer.server.ipc_server"`` for backward
compatibility: existing log-scraping tests (``test_logging_format``,
``test_ipc5_error_envelope_parity``) assert this name in their
``caplog`` assertions, and the dispatcher's outer ``except Exception``
in ``voice_typer/server/ipc/server.py`` also uses this name — so
handler-emitted records and dispatcher-emitted records land under the
same logger in the unified ``voice-typer.log``.

This module is import-safe: it has no side effects beyond defining the
module-level ``log`` object. It does NOT configure handlers (the app's
``_setup_logging`` does that, once, at startup).
"""

from __future__ import annotations

import logging

# CR-63: single source of truth for the IPC handler logger name.
# Keep this name in sync with the dispatcher's logger
# (``voice_typer/server/ipc/server.py`` and
# ``voice_typer/server/ipc_server.py``) so handler and dispatcher log
# records are emitted under the same logger in ``voice-typer.log``.
log = logging.getLogger("voice_typer.server.ipc_server")

__all__ = ["log"]
