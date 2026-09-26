"""Lausu, centralized logging infrastructure."""

from __future__ import annotations

import logging
import os  # noqa: F401  (re-exported: tests patch vt_log.os.name)

# Centralized log-retention constants.  Mirror the Rust-side
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
_session_id: str = ""
"""8-char hex session ID, generated once per :func:`setup_logging` call."""
