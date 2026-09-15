"""Voice Typer, centralized logging infrastructure.

This package is the single source of truth for all logging configuration
across the application.  Every backend module should obtain its logger
via ``logging.getLogger(__name__)`` directly (the standard Python
idiom)::

    import logging
    log = logging.getLogger(__name__)

The main entry point (typically ``app.py``) must call :func:`setup_logging`
**once** at process startup to configure file and console handlers.

Components
----------

- :func:`setup_logging`: one-time file + console configuration
- :func:`close_devnull_files`: shutdown cleanup
- :func:`reset`: test isolation
- :class:`_SessionFilter`: injects ``session_id`` into log records
- :class:`_ColorFormatter`: ANSI-coloured terminal formatter (default)
- :class:`_FileFormatter`: plain-text file formatter (default)
- :class:`_JsonFormatter`: structured JSON formatter (opt-in, ``VOICE_TYPER_LOG_JSON=1``)
- :func:`set_correlation_id` / :func:`get_correlation_id` / :func:`reset_correlation_id` /
  :class:`_correlation_id`: correlation-id context propagation

Package layout
--------------

This module was originally a single ``log.py`` file. It has been split
into a package for maintainability:

- :mod:`voice_typer.server.log.correlation`: correlation-id context vars
- :mod:`voice_typer.server.log.formatters`: the three formatter classes
  (:class:`_ColorFormatter`, :class:`_FileFormatter`, :class:`_JsonFormatter`)
  plus their supporting helpers (topic tables, ISO-timestamp formatter,
  exception-text appender)
- :mod:`voice_typer.server.log.state`: mutable process-wide registries
  (``_module_level_overrides``, ``_devnull_files``) plus
  :func:`reset` / :func:`close_devnull_files` / :func:`register_devnull_file`
- :mod:`voice_typer.server.log.handlers`: filters and handler classes
  (:class:`_SessionFilter`, :class:`_BubbleLevelExclusionFilter`,
  :class:`_FlushingStreamHandler`, :class:`_SecureTruncatingFileHandler`)
- :mod:`voice_typer.server.log.setup`: path helpers, legacy-log
  migration, retention sweep, per-module levels, and
  :func:`setup_logging`

This ``__init__`` module re-exports every public (and historically
imported private) name so ``from voice_typer.server.log import X``
and ``voice_typer.server.log.X`` continue to resolve exactly as before
the split.  No behavior change, same public API, same tests pass.

Monkeypatch contract
--------------------

``setup_logging`` resolves ``_sweep_stale_logs`` and
``_SecureTruncatingFileHandler`` via this package object at call time
so tests that ``monkeypatch.setattr(voice_typer.server.log, ...)``
keep working after the split.  ``_session_id`` lives on this package
module (not in a submodule) because test fixtures snapshot/restore it
via ``_log_module._session_id``.
"""

from __future__ import annotations

import logging
import os  # noqa: F401  (re-exported: tests patch vt_log.os.name)

# Centralized log-retention constants.  Mirror the Rust-side
# ``LOG_MAX_BYTES`` in ``src-tauri/src/util.rs``.
# All Python logging handlers that write log files (the main
# voice-typer.log, the prewarm.log, and the Electron-build log) MUST
# import the size cap from here instead of inlining ``5 * 1024 * 1024``
# so a future bump edits ONE file.  See
# ``voice_typer/server/_log_constants.py`` for the three-tier rationale.
#
# Single-file policy: each log is a SINGLE file.  When it exceeds
# ``LOG_MAX_BYTES`` (the Tier-3 mid-session hard ceiling) it is
# truncated in place (emptied) and writing continues, numbered backups
# (``.1``, ``.2``, ...) are NEVER created.  Tiers 1 (age) and 2 (size
# fallback) run at session start via :func:`_sweep_stale_logs`.
from voice_typer.server._log_constants import (
    LOG_AGE_RETENTION_SECONDS,  # noqa: F401  (re-exported for tests)
    LOG_MAX_BYTES,  # noqa: F401  (re-exported)
    LOG_SIZE_FALLBACK_BYTES,  # noqa: F401  (re-exported)
)
from voice_typer.server.log.correlation import (  # noqa: F401
    _correlation_id,
    _correlation_id_ctx,
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)
from voice_typer.server.log.formatters import (  # noqa: F401
    _TOPIC_COLOR,
    _TOPIC_KEYWORDS,
    _TOPIC_KEYWORDS_REGEX,
    _append_exception_text,
    _build_topic_keywords_regex,
    _ColorFormatter,
    _extract_topic,
    _FileFormatter,
    _infer_topic,
    _iso_timestamp,
    _JsonFormatter,
)
from voice_typer.server.log.handlers import (  # noqa: F401
    _BubbleLevelExclusionFilter,
    _emit_reentrancy,
    _FlushingStreamHandler,
    _quiet_handler_error,
    _SecureTruncatingFileHandler,
    _SessionFilter,
    _stderr_line,
)
from voice_typer.server.log.setup import (  # noqa: F401
    _LEGACY_LOG_GLOBS,
    _LEGACY_LOG_NAMES,
    _THIRD_PARTY_LOGGER_LEVELS,
    LOG_SUBDIR,
    _apply_per_module_log_levels,
    _apply_third_party_logger_levels,
    _ensure_last_resort_redacted,
    _json_logging_enabled,
    _maybe_migrate_legacy_logs,
    _maybe_move_legacy_log_file,
    _sweep_stale_logs,
    get_log_file_path,
    get_logs_dir,
    get_module_levels,
    set_module_level,
    setup_logging,
)
from voice_typer.server.log.state import (  # noqa: F401
    _devnull_files,
    _module_level_overrides,
    close_devnull_files,
    register_devnull_file,
    reset,
)

log = logging.getLogger(__name__)

# Canonical store for the 8-char hex session ID.  Lives on THIS module
# (not a submodule) so test fixtures that snapshot/restore
# ``_log_module._session_id`` and ``_SessionFilter`` (which late-looks
# up the package attribute) observe the same value.
_session_id: str = ""
"""8-char hex session ID, generated once per :func:`setup_logging` call."""
