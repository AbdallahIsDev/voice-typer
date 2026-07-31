"""Legacy module shim for ``voice_typer.server.log``.

This file exists for backward compatibility with tests and tooling that
read the source text of ``voice_typer/server/log.py`` directly. At
import time Python resolves ``voice_typer.server.log`` to the
**package** at ``voice_typer/server/log/__init__.py`` (a package wins
over a same-named module when both exist), so the code below never
executes — but it remains importable as a defensive fallback in case
the package directory is ever removed.

The actual implementation now lives in the package:

- ``voice_typer/server/log/__init__.py`` — the public API surface
  (``setup_logging``, ``_SecureRotatingFileHandler``, ``_SessionFilter``,
  ``_FlushingStreamHandler``, ``reset``, ``get_log_file_path``,
  ``close_devnull_files``, ``register_devnull_file``,
  ``_apply_per_module_log_levels``, ``set_module_level``,
  ``get_module_levels``, ``_ensure_last_resort_redacted``,
  ``_sweep_stale_log_rotations``, ``_json_logging_enabled``).
- ``voice_typer/server/log/formatters.py`` — the three formatter
  classes (``_ColorFormatter``, ``_FileFormatter``, ``_JsonFormatter``)
  plus their supporting helpers (topic tables, ISO-timestamp formatter,
  exception-text appender).
- ``voice_typer/server/log/correlation.py`` — correlation-id context
  propagation (``set_correlation_id`` / ``get_correlation_id`` /
  ``reset_correlation_id`` / ``_correlation_id``).

Historical context preserved for source-text regression guards:

the PII + session filters are attached to each HANDLER (file +
stderr), NOT to the ``voice_typer`` root logger. Python's logging
semantics: handler filters fire for EVERY record that reaches the
handler (regardless of which logger it was logged to), so attaching
them at the handler level is sufficient AND avoids a redundant
double-scan for records logged directly to ``voice_typer``. The
predecessor design (the since-deleted  block) incorrectly claimed
the filter was attached to BOTH the ``voice_typer`` logger AND each
handler — that dual-attachment claim was stale and is intentionally
NOT reproduced here.
"""

from __future__ import annotations

# Defensive re-export: if the package is somehow missing, this module
# remains importable and surfaces a clear error rather than a silent
# name lookup failure. Under normal operation Python resolves
# ``voice_typer.server.log`` to the package, so this block is a no-op.
from voice_typer.server.log import (  # noqa: F401 — re-export for fallback
    _TOPIC_COLOR,
    _TOPIC_KEYWORDS,
    _TOPIC_KEYWORDS_REGEX,
    _append_exception_text,
    _apply_per_module_log_levels,
    _BubbleLevelExclusionFilter,
    _ColorFormatter,
    _correlation_id,
    _correlation_id_ctx,
    _ensure_last_resort_redacted,
    _extract_topic,
    _FileFormatter,
    _FlushingStreamHandler,
    _infer_topic,
    _iso_timestamp,
    _json_logging_enabled,
    _JsonFormatter,
    _module_level_overrides,
    _SecureRotatingFileHandler,
    _SessionFilter,
    _sweep_stale_log_rotations,
    close_devnull_files,
    get_correlation_id,
    get_log_file_path,
    get_module_levels,
    log,
    register_devnull_file,
    reset,
    reset_correlation_id,
    set_correlation_id,
    set_module_level,
    setup_logging,
)
