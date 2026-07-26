"""Shared logger for the IPC handler mixin package (CR-63).

ARCH-REFAC-002 / CR-63: prior to this module, every handler mixin
under ``voice_typer/server/handlers/`` opened its own copy of::

    log = logging.getLogger("voice_typer.server.ipc_server")

That duplication was harmless at runtime (``logging.getLogger`` returns
the same logger object for a given name) but it made the mixins harder
to audit: a reader had to verify that every copy used the *same*
logger name, and a future rename (e.g. splitting ``ipc_server`` from
``ipc.server``) would have required touching every handler file.

This module is the single source of truth for the handler-package
logger name. Mixins import ``log`` from here::

    from voice_typer.server.handlers._log import log

The consolidation is COMPLETE: 0 handler mixins declare
``log = logging.getLogger(...)`` inline. 9 handler modules plus the
shared base class import ``log`` from this module:

- ``_base.py`` (the HandlerBase base class — uses ``log`` in
  ``_respond_with_error``, so all 6 handler mixins that inherit
  ``HandlerBase`` indirectly emit through this logger)
- ``config_handlers.py``
- ``history_handlers.py``
- ``model_handlers.py``
- ``onboarding_handlers.py``
- ``privacy_handlers.py``
- ``status_handlers.py``
- ``system_handlers.py``
- ``templates_handlers.py``
- ``vocabulary_handlers.py``

The remaining handler mixins (``dictation_handlers.py``,
``level_monitor_handlers.py``, ``microphone_handlers.py``,
``microphone_test_handlers.py``, ``repaste_handlers.py``,
``vocabulary_automation_handlers.py``) do NOT use logging directly and
therefore do not need to import ``log`` — their error paths route
through ``HandlerBase._respond_with_error`` (which uses the shared
``log``).  New handlers that need to emit log records SHOULD import
``log`` from this module rather than re-declaring the logger inline,
so the consolidation is preserved.

The logger name stays ``"voice_typer.server.ipc_server"`` for backward
compatibility: existing log-scraping tests (``test_logging_format``,
``test_ipc5_error_envelope_parity``) assert this name in their
``caplog`` assertions, and the dispatcher's outer ``except Exception``
in ``voice_typer/server/ipc_server.py`` also uses this name — so
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
# (``voice_typer/server/ipc_server.py``) so handler and dispatcher log
# records are emitted under the same logger in ``voice-typer.log``.
log = logging.getLogger("voice_typer.server.ipc_server")

__all__ = ["log"]
