"""Voice Typer — centralized logging infrastructure.

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

- :func:`setup_logging` — one-time file + console configuration
- :func:`close_devnull_files` — shutdown cleanup
- :func:`reset` — test isolation
- :class:`_SessionFilter` — injects ``session_id`` into log records
- :class:`_ColorFormatter` — ANSI-coloured terminal formatter (default)
- :class:`_FileFormatter` — plain-text file formatter (default)
- :class:`_JsonFormatter` — structured JSON formatter (opt-in, ``VOICE_TYPER_LOG_JSON=1``)
- :func:`set_correlation_id` / :func:`get_correlation_id` / :func:`reset_correlation_id` /
  :class:`_correlation_id` — correlation-id context propagation

Package layout
--------------

This module was originally a single ``log.py`` file. It has been split
into a package for maintainability:

- :mod:`voice_typer.server.log.correlation` — correlation-id context vars
- :mod:`voice_typer.server.log.formatters` — the three formatter classes
  (:class:`_ColorFormatter`, :class:`_FileFormatter`, :class:`_JsonFormatter`)
  plus their supporting helpers (topic tables, ISO-timestamp formatter,
  exception-text appender)

This ``__init__`` module re-exports the formatter classes and the
correlation-id helpers so ``from voice_typer.server.log import X``
continues to resolve every public name that the original ``log.py``
exposed. No behavior change — same public API, same tests pass.
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import sys
import time
import uuid
from pathlib import Path

# WN-7: centralized log-rotation constants.  Mirrors the Rust-side
# ``ROTATE_MAX_BYTES`` / ``ROTATE_MAX_FILES`` in ``src-tauri/src/util.rs``.
# All Python logging handlers that create ``RotatingFileHandler`` instances
# (the main voice-typer.log, the prewarm.log, and the Electron-build log)
# MUST import these instead of inlining ``5 * 1024 * 1024`` / ``5`` so a
# future bump to the rotation policy edits ONE file.  See
# ``voice_typer/server/_log_constants.py`` for the rationale.
from voice_typer.server._log_constants import (  # noqa: F401
    ROTATE_MAX_BYTES,
    ROTATE_MAX_FILES,
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

log = logging.getLogger(__name__)

# log retention sweep — purge rotated log files older than
# 30 days at startup. Mirrors ``crash_handler._sweep_stale_diagnostics``
# (30-day mtime cutoff for crash diagnostics). The size-only rotation
# (5 MiB × 5 backups) keeps at most 25 MiB of recent logs but does NOT
# bound the age — a low-traffic install could keep a 6-month-old
# ``voice-typer.log.5`` containing dictated-text fragments (01 /
# indefinitely. The startup sweep closes that gap by
# unlinking any rotated file older than 30 days, regardless of how
# many backups are currently retained.
_LOG_RETENTION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days
_LOG_ROTATION_GLOBS: tuple[str, ...] = (
    "voice-typer.log.*",  # main process rotations (``.1``..``.5``)
    "prewarm.log.*",  # legacy prewarm process rotations
    "voice-typer-prewarm.log.*",  # prewarm process rotations
)


def _sweep_stale_log_rotations(config_dir: Path) -> None:
    """Delete rotated log files older than 30 days .

    Mirrors ``crash_handler._sweep_stale_diagnostics``: enumerate
    ``voice-typer.log.*`` and ``prewarm.log.*`` in ``config_dir``,
    unlink any whose ``st_mtime`` is older than
    ``_LOG_RETENTION_MAX_AGE_SECONDS``. Best-effort — any error is
    logged at DEBUG and swallowed so a single unreadable file does not
    abort the sweep or ``setup_logging``.

    The active log files (``voice-typer.log``, ``prewarm.log`` — no
    suffix) are NEVER touched: they are the live sinks for the current
    process. Only the numbered rotations (``.1``, ``.2``, ...) are
    candidates for purging.

    Called from :func:`setup_logging` after the file handler is
    installed so the sweep runs once per process startup. Idempotent
    if called multiple times (subsequent calls find nothing to delete).
    """
    try:
        root = Path(config_dir).resolve()
        if not root.is_dir():
            return
        now = time.time()
        for pattern in _LOG_ROTATION_GLOBS:
            for f in root.glob(pattern):
                # Skip the live file (no numeric suffix). The glob
                # ``voice-typer.log.*`` already excludes the bare
                # ``voice-typer.log`` (no dot-suffix match), but be
                # defensive — a stray ``voice-typer.log.rotate.lock``
                # or similar should not be unlinked by the sweep.
                if not f.is_file():
                    continue
                # NEVER touch the inter-process rotation
                # lock file (``voice-typer.log.rotate.lock`` /
                # ``prewarm.log.rotate.lock``). It is created by
                # ``_SecureRotatingFileHandler.__init__`` and must
                # persist across setups so the next process can
                # acquire the flock. Deleting it would race with a
                # concurrent writer's lock acquisition.
                if f.name.endswith(".rotate.lock"):
                    continue
                try:
                    age = now - f.stat().st_mtime
                except OSError:
                    continue
                if age > _LOG_RETENTION_MAX_AGE_SECONDS:
                    try:
                        f.unlink()
                        log.debug(
                            "[LOG-SETUP] purged stale log rotation %s (age=%.0f days)",
                            f.name,
                            age / 86400,
                        )
                    except OSError as exc:
                        log.debug(
                            "[LOG-SETUP] failed to purge stale log rotation %s: %s",
                            f.name,
                            exc,
                        )
    except Exception as exc:  # noqa: BLE001 — best-effort sweep
        log.debug("[LOG-SETUP] stale-log-rotation sweep failed: %s", exc)


# ── Module-level state ────────────────────────────────────────────────
# Encapsulated here instead of in a class so it's accessible to filters
# and formatters without passing references through the logging framework.

_session_id: str = ""
"""8-char hex session ID, generated once per :func:`setup_logging` call."""


def _json_logging_enabled() -> bool:
    """structured JSON logging is opt-in via ``VOICE_TYPER_LOG_JSON``.

    Keeps the human-readable text format as the default so existing
    operator workflows (grep, tail) are unaffected.
    """
    return os.environ.get("VOICE_TYPER_LOG_JSON", "").lower() in ("1", "true", "yes")


_devnull_files: list = []
"""File descriptors opened for pythonw.exe stdio redirection."""


# runtime log-level override registry.  Populated by
# ``_apply_per_module_log_levels`` (env-var path at startup) and by
# ``set_module_level`` (runtime API for IPC / CLI).  Queried by
# ``get_module_levels`` so operators can verify the active per-module
# config without restarting.  Values are stored as level *names*
# (``"DEBUG"``, ``"INFO"`` ...) so the dict is JSON-serialisable for IPC.
_module_level_overrides: dict[str, str] = {}


def reset() -> None:
    """Reset all logging state — called by tests to avoid cross-test contamination."""
    global _session_id
    _session_id = ""
    for f in _devnull_files:
        with contextlib.suppress(Exception):
            f.close()
    _devnull_files.clear()
    # clear the per-module override registry so tests don't leak
    # overrides between runs.
    _module_level_overrides.clear()
    root = logging.getLogger("voice_typer")
    root.handlers.clear()
    root.filters.clear()
    root.setLevel(logging.DEBUG)


# ── Session filter ────────────────────────────────────────────────────


class _SessionFilter(logging.Filter):
    """Inject ``session_id`` and ``component`` attributes into every record.

    The ``session_id`` is set once per process by :func:`setup_logging`.
    The ``component`` defaults to the logger name so formatters can show
    which module produced the record.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "session_id"):
            record.session_id = _session_id
        if not hasattr(record, "component"):
            record.component = record.name
        return True


class _BubbleLevelExclusionFilter(logging.Filter):
    """ADR-0020 §11: keep ``bubble_level`` records out of the file log.

    The ``bubble_level`` event is a high-frequency (~60 Hz, coalesced
    to ≤30 Hz on the Tauri host per §9) RMS/peak push used only for
    the live waveform bubble. It carries no diagnostic value in the
    rotating file and would dominate the on-disk log. The console/stderr
    handler is unaffected — only the file handler attaches this filter.

    The marker string is the exact event-type token used by the
    ``bubble_level`` push and by ``IPCServer._send``'s high-frequency
    drop log, so any record mentioning it is excluded from the file.

    WARNING+ records are kept unconditionally (cheap path,
    no ``getMessage()`` call) so legitimate error logs mentioning
    ``bubble_level`` are never silently dropped from the file.
    """

    _MARKER = "bubble_level"

    def filter(self, record: logging.LogRecord) -> bool:
        # cheap path — WARNING+ records are always kept (no
        # ``getMessage()`` call) so a legitimate
        # ``"bubble_level handler crashed"``-style error is never
        # dropped from the file. The expensive substring match only
        # runs for DEBUG / INFO records, which is the level the
        # high-frequency bubble push is emitted at — so the
        # noise-suppression behaviour is preserved while protecting
        # diagnostic error lines.
        if record.levelno >= logging.WARNING:
            return True
        # hybrid check. ``record.msg`` is the raw template
        # string (no %-format substitution) — checking it first avoids
        # the ``getMessage()`` call on every DEBUG/INFO record. Fall
        # back to ``getMessage()`` only when the record carries
        # positional args (i.e. the marker could appear in the
        # substituted output but not the template). The hot path is a
        # literal ``"bubble_level"`` log call with no args, so the
        # cheap ``in record.msg`` check covers it; the fallback
        # preserves correctness for callers that interpolate the
        # marker via ``%s``.
        if not record.args:
            return self._MARKER not in record.msg
        return self._MARKER not in record.getMessage()


# ── One-time setup ────────────────────────────────────────────────────


def _apply_per_module_log_levels() -> None:
    """Apply per-module log level overrides from ``VOICE_TYPER_LOG_LEVEL_MODULES``.

    Format::

        VOICE_TYPER_LOG_LEVEL_MODULES="module.path=LEVEL,another.module=LEVEL"

    where ``LEVEL`` is a ``logging`` level name (``DEBUG``, ``INFO``,
    ``WARNING``, ``ERROR``, ``CRITICAL``).  Invalid entries are
    skipped (best-effort) so a typo in one entry does not break
    logging setup, but each skipped entry now logs a WARNING
    so the operator can see *which* entry was ignored and why — a
    silent skip was an operator trap (typo in the module path => no
    DEBUG output => operator assumes the subsystem isn't logging when
    in fact the override never applied).  Lets operators crank up
    DEBUG on a single subsystem (e.g.
    ``voice_typer.server.dictation_pipeline``) without enabling DEBUG
    globally and flooding the rotating file with high-frequency events
    from unrelated subsystems.

    Successfully applied overrides are recorded in
    :data:`_module_level_overrides` so :func:`get_module_levels` can
    report the active per-module config .
    """
    raw = os.environ.get("VOICE_TYPER_LOG_LEVEL_MODULES", "")
    if not raw:
        return
    # log to the voice_typer.server.log logger so the warning
    # reaches the rotating file handler (setup_logging has already
    # attached it by the time this runs).
    setup_log = logging.getLogger("voice_typer.server.log")
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            setup_log.warning(
                "[LOG-SETUP] skipping invalid VOICE_TYPER_LOG_LEVEL_MODULES entry %r (reason: missing '=')",
                entry,
            )
            continue
        name, _, level_str = entry.partition("=")
        name = name.strip()
        level_str = level_str.strip().upper()
        if not name or not level_str:
            setup_log.warning(
                "[LOG-SETUP] skipping invalid VOICE_TYPER_LOG_LEVEL_MODULES "
                "entry %r (reason: empty module name or level)",
                entry,
            )
            continue
        level = getattr(logging, level_str, None)
        if not isinstance(level, int):
            setup_log.warning(
                "[LOG-SETUP] skipping invalid VOICE_TYPER_LOG_LEVEL_MODULES "
                "entry %r (reason: unknown level %r — expected DEBUG/INFO/WARNING/ERROR/CRITICAL)",
                entry,
                level_str,
            )
            continue
        logging.getLogger(name).setLevel(level)
        # record the override so get_module_levels can report it.
        _module_level_overrides[name] = level_str
        setup_log.info(
            "[LOG-SETUP] set %s to %s",
            name,
            level_str,
        )


def set_module_level(name: str, level: str) -> None:
    """Set a single logger's level at runtime .

    Parameters
    ----------
    name:
        Dotted logger name (e.g. ``"voice_typer.server.dictation_pipeline"``).
    level:
        Level name (``"DEBUG"``, ``"INFO"``, ``"WARNING"``, ``"ERROR"``,
        ``"CRITICAL"``) — case-insensitive.  Invalid names raise
        :class:`ValueError`.

    Notes
    -----
    Mirrors what :func:`_apply_per_module_log_levels` does for the
    ``VOICE_TYPER_LOG_LEVEL_MODULES`` env var, but exposes a public
    API so the renderer / a future CLI / a debug overlay can change
    a subsystem's level without restarting the sidecar.  Emits an
    INFO log line so the change is visible in the rotating file (audit
    trail).  The override is recorded in :data:`_module_level_overrides`
    and is queryable via :func:`get_module_levels`.
    """
    if not name or not isinstance(name, str):
        raise ValueError(f"set_module_level: name must be a non-empty string, got {name!r}")
    level_str = (level or "").strip().upper()
    resolved = getattr(logging, level_str, None) if level_str else None
    if not isinstance(resolved, int):
        raise ValueError(
            f"set_module_level: unknown level {level!r} for module {name!r} "
            "(expected DEBUG/INFO/WARNING/ERROR/CRITICAL)"
        )
    logging.getLogger(name).setLevel(resolved)
    _module_level_overrides[name] = level_str
    logging.getLogger("voice_typer.server.log").info(
        "[LOG-SETUP] set %s to %s (runtime override)",
        name,
        level_str,
    )


def get_module_levels() -> dict[str, str]:
    """Return a snapshot of explicitly-set per-module level overrides .

    Returns a fresh dict (mutating the return value does not affect
    internal state).  Includes overrides applied by the
    ``VOICE_TYPER_LOG_LEVEL_MODULES`` env var at startup AND by
    subsequent :func:`set_module_level` calls.  Values are level
    *names* (``"DEBUG"`` ...) so the dict is JSON-serialisable for IPC.
    """
    return dict(_module_level_overrides)


def _ensure_last_resort_redacted(pii_filter: logging.Filter) -> None:
    """Ensure the global ``lastResort`` handler carries ``PIIRedactionFilter``.

    Third-party loggers (``keyring``, ``urllib3``, ``websockets``)
    propagate to the root logger; when the root logger has no handlers
    the ``lastResort`` ``_StderrHandler`` fires.  Without
    ``PIIRedactionFilter`` attached, any secret those libraries log
    (e.g. a buggy keyring backend logging the secret value) bypasses
    the redaction pipeline and lands in stderr verbatim.  Attach the
    filter idempotently so repeated ``setup_logging`` calls do not
    double-attach.
    """
    last_resort = getattr(logging, "lastResort", None)
    if last_resort is None:
        return
    # Idempotent: skip if a PIIRedactionFilter of the same class is
    # already attached.
    # use ``isinstance(f, type(pii_filter))`` instead of the
    # string-based ``type(f).__name__ == "PIIRedactionFilter"`` check.
    # The string check is brittle: a future subclass, rename, or
    # monkeypatch (e.g. a test double named differently but inheriting
    # from ``PIIRedactionFilter``) would silently bypass the idempotency
    # guard and double-attach. The isinstance check is type-safe and
    # survives subclassing.
    if any(isinstance(f, type(pii_filter)) for f in last_resort.filters):
        return
    last_resort.addFilter(pii_filter)


def setup_logging(
    config_dir: Path,
    *,
    debug: bool = False,
    quiet: bool = False,
    port_mode: bool = False,
    process_name: str = "main",
) -> str:
    """Configure Voice Typer logging — rotating file + optional coloured console.

    Call this **once** at process startup, before any subsystem logs.
    It is safe to call multiple times (subsequent calls are idempotent).

    Parameters
    ----------
    config_dir:
        Directory where the rotating log file will be created.
    debug:
        If ``True``, the stderr handler AND the rotating file handler
        emit DEBUG-level messages .  When ``False`` both
        handlers sit at INFO so production runs do not churn through
        5 MiB x 5 of DEBUG noise.
    quiet:
        If ``True``, the file handler is set to WARNING level
        (reduces telemetry noise for enterprise deployments).
    port_mode:
        If ``True``, enables coloured stderr output even when not
        connected to a TTY (used by ``--port`` mode).

    Returns
    -------
    The 8-character hex session ID for this process.
    """
    global _session_id

    # tighten the process umask to 0o077 while creating log
    # files so they are world-unreadable on POSIX even if the parent dir
    # perms are loose.  ``mkdir`` + ``RotatingFileHandler`` consult the
    # umask when computing the on-disk mode, so 0o077 yields 0o700 dirs
    # and 0o600 files.  Restored in ``finally`` so the umask change
    # does not leak to subprocesses spawned after setup_logging returns.
    _old_umask = os.umask(0o077)
    try:
        # ── 1. Redirect stdio for pythonw.exe ──────────────────────
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")  # noqa: SIM115 — must outlive setup_logging()
            _devnull_files.append(sys.stderr)
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")  # noqa: SIM115 — must outlive setup_logging()
            _devnull_files.append(sys.stdout)
        if sys.stdin is None:
            sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115 — must outlive setup_logging()
            _devnull_files.append(sys.stdin)

        # ── 2. Generate session ID ─────────────────────────────────────
        _session_id = uuid.uuid4().hex[:8]

        # ── 3. Rotating file handler  ────────────────────────
        config_dir.mkdir(parents=True, exist_ok=True)
        # lock down the config dir itself so co-located users
        # cannot ``cat`` the log file even if the per-file chmod is missed
        # (defence in depth — both this and the per-file chmod below are
        # best-effort on Windows where POSIX perms do not apply).
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(config_dir, 0o700)
        # Route to voice-typer-prewarm.log when
        # process_name == "prewarm" so the prewarm and main processes don't
        # race on the same file.
        log_file = get_log_file_path(config_dir, process_name=process_name)

        # structured JSON logging is opt-in via VOICE_TYPER_LOG_JSON.
        # When enabled, the file (and console, below) use _JsonFormatter so
        # aggregation tools get one JSON object per line.  The PIIRedactionFilter
        # still runs first (attached below), so JSON output is redacted exactly
        # like the text output.  Human-readable text remains the default.
        json_mode = _json_logging_enabled()
        _file_formatter = _JsonFormatter() if json_mode else _FileFormatter()

        # use ``errors='backslashreplace'`` so Unicode
        # characters that can't be encoded in the system locale (cp1252
        # on Windows, e.g. → → right arrow) are escaped as \\uXXXX
        # instead of being silently replaced with the � replacement
        # character.  Without this, valuable diagnostic symbols like
        # arrows, em-dashes, and smart quotes become unreadable trash
        # in the log file.
        handler = _SecureRotatingFileHandler(
            log_file,
            # ADR-0020 §11: 5 MiB per file, keep 5 backups (was 1 MiB × 2).
            # WN-7: the rotation policy is centralized in
            # ``voice_typer.server._log_constants`` so the main log,
            # prewarm log, and (eventually) the Electron-build log all
            # share a single source of truth — a future bump to 10 MiB
            # or 7 backups edits ONE file instead of three.
            maxBytes=ROTATE_MAX_BYTES,
            backupCount=ROTATE_MAX_FILES,
            encoding="utf-8",
            errors="backslashreplace",
        )
        # lock down the log file itself (0o600 — only the
        # owning user can read dictated-text previews, exception
        # tracebacks, and hotkey registrations).  Best-effort on POSIX;
        # silently no-op on Windows where the umask already enforced
        # 0o600 at creation time via the ``os.umask(0o077)`` above.
        # post-rotation re-chmod is handled by
        # ``_SecureRotatingFileHandler.doRollover`` so the privacy
        # guarantee survives log rotation (which happens AFTER
        # setup_logging returns and the umask is restored).
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(log_file, 0o600)
        # gate the file handler on the ``debug`` flag so
        # production runs do not churn through 5 MiB × 5 of DEBUG noise.
        # Root stays at DEBUG so child loggers can still emit DEBUG
        # records when ``VOICE_TYPER_DEBUG=1`` is set — the handler
        # filter is what actually drops them at INFO level.
        # gate the file handler on ``debug`` AND ``quiet`` so
        # the handler level matches the root logger level set below.
        # Pre-fix ``quiet=True`` raised the root to WARNING but
        # left the file handler at INFO — the handler still wanted INFO
        # records but the root logger filtered them out before they
        # reached any handler, so the effective file verbosity did not
        # match the ``quiet`` contract. Now ``quiet=True`` lowers the
        # file handler to WARNING too, matching the root logger.
        handler.setLevel(logging.WARNING if quiet else (logging.DEBUG if debug else logging.INFO))
        # ADR-0020 §11: keep high-frequency ``bubble_level`` events out of
        # the rotating file log. They are ~60 Hz RMS/peak pushes (ADR-0020
        # §9 coalesces to ≤30 Hz on the host) and carry no diagnostic
        # value in the file — they only exist for the live waveform bubble.
        # The console/stderr path is unchanged. The filter drops any record
        # whose message mentions "bubble_level" (the exact marker used by
        # IPCServer._send's high-frequency drop log and the bubble event
        # type), so the file stays small and readable.
        handler.addFilter(_BubbleLevelExclusionFilter())
        handler.setFormatter(_file_formatter)

        # PII / API-key redaction — imported lazily to avoid circular imports
        # and to keep the security module's import order clean.
        # The filter is attached
        # to each HANDLER (file + stderr), NOT to the ``voice_typer`` root
        # logger. Python's logging semantics: handler filters fire for
        # EVERY record that reaches the handler (regardless of which
        # logger it was logged to), so attaching at the handler level is
        # sufficient AND avoids a redundant double-scan for records
        # logged directly to ``voice_typer``. See the  comment
        # block below the handler-installation block for the full
        # rationale.
        from voice_typer.server.security import PIIRedactionFilter as _PIIRedactionFilter

        _pii_filter = _PIIRedactionFilter()
        handler.addFilter(_pii_filter)
        # Attach ``_SessionFilter`` to the file handler
        # too — not just the ``voice_typer`` logger — so the session_id is
        # injected for records logged to *child* loggers (e.g.
        # ``voice_typer.server.app``) which do NOT trigger the parent
        # logger's filters per Python's logging semantics (``callHandlers``
        # invokes handler filters, not ancestor-logger filters).  The
        # filter is idempotent (``hasattr`` guard), so double-filtering a
        # record that already hit the logger-level filter is harmless.
        _session_filter = _SessionFilter()
        handler.addFilter(_session_filter)

        root = logging.getLogger("voice_typer")
        # Avoid duplicate handlers if setup is called multiple times.
        # dedup on the ``_SecureRotatingFileHandler`` subclass
        # (not the parent ``RotatingFileHandler``) so a future caller
        # that installs a stock ``RotatingFileHandler`` (e.g. a test
        # helper) is NOT mistaken for the secure handler — the secure
        # handler is always re-installed in that case so the perms and
        # inter-process lock guarantees are preserved.
        #
        # pre-fix, the dedup check SILENTLY DROPPED the new
        # handler when one was already installed. A second
        # ``setup_logging(config_dir, debug=False)`` call (after an
        # initial ``debug=True``) constructed a new handler with the
        # new WARNING level but then threw it away — the existing
        # DEBUG-level handler stayed attached, so the operator's
        # toggle had NO effect (despite the function returning a new
        # ``session_id`` suggesting re-init succeeded). Post-fix, the
        # existing handler's level + formatter are UPDATED IN PLACE
        # to match the new configuration before the dedup check
        # decides whether to add the new handler. Filters (PII /
        # session / bubble) are not re-attached — they're already on
        # the existing handler from the first call.
        _new_file_level = handler.level
        _new_file_formatter = handler.formatter
        for _existing in root.handlers:
            if isinstance(_existing, _SecureRotatingFileHandler):
                _existing.setLevel(_new_file_level)
                if _new_file_formatter is not None:
                    _existing.setFormatter(_new_file_formatter)
        if not any(isinstance(h, _SecureRotatingFileHandler) for h in root.handlers):
            root.addHandler(handler)
        # PII + session filters are attached to each HANDLER
        # (file + stderr) above, NOT to the ``voice_typer`` root logger.
        # Python's logging semantics: handler filters fire for EVERY
        # record that reaches the handler (regardless of which logger
        # it was logged to), so attaching them at the handler level is
        # sufficient AND avoids a redundant double-scan for records
        # logged directly to ``voice_typer`` (which would otherwise
        # trigger the filter once at the logger level and again at the
        # handler level). The previous dual attachment was intentional
        # but the handler-only path covers child-logger records too --
        # ``callHandlers`` walks ancestor loggers but invokes handler
        # filters, not ancestor-LOGGER filters.

        root.setLevel(logging.DEBUG)

        # quiet mode for enterprise deployments
        if quiet:
            root.setLevel(logging.WARNING)

        # Per-module log level overrides (env: VOICE_TYPER_LOG_LEVEL_MODULES).
        # Applied AFTER root level set so they take precedence over the root
        # default — operators can crank DEBUG on a single subsystem without
        # enabling DEBUG globally.
        _apply_per_module_log_levels()

        # Ensure the global ``lastResort`` handler also
        # carries PIIRedactionFilter so third-party loggers (keyring,
        # urllib3, websockets) that bypass voice_typer's handlers do not
        # leak secrets via the fallback stderr path.
        _ensure_last_resort_redacted(_pii_filter)

        # ── 4. Fix stderr encoding for Unicode ─────────────────────────
        if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
            with contextlib.suppress(OSError):
                sys.stderr.reconfigure(errors="backslashreplace")

        # ── 5. Stderr stream handler ─────────────────────────────────────
        # always flush after each emit so terminal log lines appear
        # in real-time.  The bare logging.StreamHandler only flushes on
        # close (or when its internal buffer hits a high-water mark), so
        # startup logs from a standalone VoiceTyper run could sit in the
        # buffer for seconds before being flushed — making the app look
        # like it's hanging silently.  _FlushingStreamHandler.emit() calls
        # self.flush() after every record.
        #
        # ALWAYS attach a stderr stream handler (with PII filter,
        # INFO level) — not only when stderr is a TTY or --port mode is
        # active.  Under Tauri sidecar, sys.stderr is a pipe (not a TTY)
        # and --port is not in sys.argv, so the legacy ``do_color`` gate
        # attached NO stream handler.  All INFO/DEBUG records went only
        # to the RotatingFileHandler; if the config dir was read-only /
        # disk full / perm wrong, the file write failed silently via
        # ``handleError`` and the record was lost.  Python's
        # ``lastResort`` only fires when NO handlers are configured —
        # here the file handler IS configured (just failing), so
        # lastResort never triggered, leaving ZERO log signal of the
        # failure.  Attaching a stderr handler guarantees the record
        # reaches *some* sink even when the file write fails.
        do_color = sys.stderr.isatty() or port_mode
        if sys.stderr is not None:
            stream = _FlushingStreamHandler()
            stream.setLevel(logging.DEBUG if debug else logging.INFO)
            if do_color:
                # in JSON mode the console also emits structured
                # records (no ANSI colouring — JSON consumers parse the
                # line, not the rendering).  The PII filter still runs
                # below, so console JSON output is redacted too.
                stream.setFormatter(_JsonFormatter() if json_mode else _ColorFormatter())
            else:
                # Non-TTY (Tauri sidecar, piped stderr, log redirection):
                # plain-text format (no ANSI escapes) so the lines stay
                # readable when piped through ``less`` / ``grep`` / a
                # log shipper.  JSON mode still uses _JsonFormatter for
                # structured consumers.
                stream.setFormatter(_JsonFormatter() if json_mode else _FileFormatter())
            # attach the same PII / API-key redaction filter to the
            # console handler so secrets don't leak to the terminal either.
            stream.addFilter(_pii_filter)
            # Same reasoning as the file handler — attach
            # ``_SessionFilter`` to the stream handler too so console output
            # also carries the session_id bracket for records from child
            # loggers.
            stream.addFilter(_SessionFilter())
            # Avoid duplicate StreamHandlers if setup is called multiple times.
            # Use _FlushingStreamHandler as the dedup key so legacy tests that
            # check for "any StreamHandler" (isinstance check below) still pass.
            #
            # mirror the file-handler in-place update — if a
            # ``_FlushingStreamHandler`` is already attached, update its
            # level + formatter to match the new ``debug``/``quiet``/
            # ``json_mode`` configuration instead of silently dropping
            # the new handler (which left the old level in effect).
            _new_stream_level = stream.level
            _new_stream_formatter = stream.formatter
            for _existing in root.handlers:
                if isinstance(_existing, _FlushingStreamHandler):
                    _existing.setLevel(_new_stream_level)
                    if _new_stream_formatter is not None:
                        _existing.setFormatter(_new_stream_formatter)
            if not any(isinstance(h, _FlushingStreamHandler) for h in root.handlers):
                root.addHandler(stream)
            # Silence noisy third-party loggers .
            for lib in ("transformers", "torch", "huggingface_hub"):
                lib_logger = logging.getLogger(lib)
                lib_logger.setLevel(logging.WARNING)
                lib_logger.handlers.clear()
                lib_logger.propagate = True

        # sweep stale log rotations (voice-typer.log.*,
        # prewarm.log.*) older than 30 days. Runs AFTER the file handler
        # is installed so the sweep's own DEBUG messages land in the
        # fresh log file. Best-effort — failures are swallowed inside
        # the helper so a single unreadable file does not abort
        # setup_logging.
        _sweep_stale_log_rotations(config_dir)

        return _session_id
    finally:
        os.umask(_old_umask)


def get_log_file_path(config_dir: Path | None = None, *, process_name: str = "main") -> Path:
    """Return the absolute path to the log file for the given process.

    used by agent 2-y for the in-app log viewer (``View Main
    Log`` button alongside ``Open Log Folder``).  Centralising the
    literal here means the viewer and ``setup_logging`` agree on the
    filename even if it ever changes.

    The ``process_name`` parameter routes
    the prewarm process to a separate ``voice-typer-prewarm.log`` file
    so the prewarm and main processes don't race on the same file.
    ``"main"`` (and any unrecognised value) routes to ``voice-typer.log``;
    ``"prewarm"`` routes to ``voice-typer-prewarm.log``.

    Parameters
    ----------
    config_dir:
        Optional override (e.g. tests pointing at ``tmp_path``).  When
        ``None``, the canonical config dir is resolved via
        :func:`voice_typer.server._paths.config_dir` (lazy import to
        avoid circular imports at module load time).
    process_name:
        ``"main"`` (default) or ``"prewarm"``. Controls which log file
        is returned.

    Returns
    -------
    Path
        ``<config_dir>/voice-typer.log`` or ``<config_dir>/voice-typer-prewarm.log``.
        The path may not yet exist on disk — callers should check ``.exists()`` before opening.
    """
    if config_dir is None:
        from voice_typer.server import _paths

        config_dir = _paths.config_dir()
    if process_name == "prewarm":
        return config_dir / "voice-typer-prewarm.log"
    return config_dir / "voice-typer.log"


class _FlushingStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes after every emit.

    the standard ``logging.StreamHandler`` only flushes on
    ``close()`` (or when its internal buffer fills up).  For an
    interactive terminal session this means startup logs can sit in
    the buffer for several seconds, making VoiceTyper look like it's
    hanging silently.  Subclassing and calling ``self.flush()`` after
    every ``emit()`` guarantees each log line is written to the
    terminal immediately.

    This subclass is also used as the dedup key in
    :func:`setup_logging` so calling ``setup_logging`` multiple times
    doesn't add duplicate console handlers.
    """

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        super().emit(record)
        # Always flush — the underlying stream may be line-buffered or
        # block-buffered, and the user wants to see logs in real time.
        with contextlib.suppress(Exception):
            # Best-effort: if the stream is closed or broken, swallow
            # so we don't mask the original log record.  (logging raises
            # the real exception via handleError; we only get here if
            # flush itself fails.)
            self.flush()


class _SecureRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """``RotatingFileHandler`` that is inter-process safe AND re-locks perms.

    Combines two concerns:
    1. Inter-process rotation safety (``fcntl.flock`` / ``msvcrt.locking``)
       so the main app and prewarm process don't race on rotation.
    2. Post-rotation ``os.chmod(self.baseFilename, 0o600)`` on POSIX so
       the active log file is never world-readable .

    The lock is held only for the brief rename+reopen window, NOT for
    every ``emit()`` call.  After acquiring the lock the handler
    re-checks whether rotation is still needed — another process may
    have rotated while we waited.
    """

    def __init__(self, filename, *args, **kwargs):
        super().__init__(filename, *args, **kwargs)
        self._rotation_lock_path = f"{filename}.rotate.lock"

    def _acquire_rotation_lock(self):
        """Open the lock file and acquire an inter-process lock on it."""
        fd = None
        try:
            if os.name == "posix":
                import fcntl

                fd = os.open(self._rotation_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                return fd
            if os.name == "nt":
                import msvcrt

                fd = os.open(self._rotation_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                # ``msvcrt.locking(LK_LOCK)`` blocks ~10s then raises
                # ``PermissionError`` (an ``OSError`` subclass) if it cannot
                # acquire the byte-range lock. The previous ``contextlib.suppress``
                # silently swallowed that, returning ``fd`` as if the lock was
                # held — two processes racing rotation could both pass. We now
                # retry once with ``LK_NBLCK`` (non-blocking) — if the holder
                # released the byte during the ~10s block, we grab it
                # instantly; if not, we fail CLOSED (close fd, return None)
                # so the caller's ``_rotation_needed()`` short-circuit kicks
                # in and no rotation is attempted without the inter-process
                # lock. Fail-closed prevents two concurrent rotations from
                # clobbering each other's rename / re-open, which previously
                # could truncate voice-typer.log.
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                except OSError:
                    # LK_LOCK timed out — try a single non-blocking acquire.
                    # If the holder released in the meantime, we succeed
                    # silently (lock is now held, no warning needed).
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    except OSError:
                        # Both LK_LOCK (10s blocking) and LK_NBLCK (instant)
                        # failed — the byte range is contended. Fail CLOSED:
                        # close the fd so it is not leaked, return None so
                        # ``doRollover`` skips rotation (no rotation without
                        # the inter-process lock). Log at WARNING (not DEBUG)
                        # so the operator can see the persistent contention.
                        log.warning(
                            "[LOG-SETUP] Windows rotation lock acquire failed "
                            "(LK_LOCK timed out, LK_NBLCK retry also failed) — "
                            "fail-closed: rotation skipped to avoid concurrent "
                            "rotation race"
                        )
                        with contextlib.suppress(OSError):
                            os.close(fd)
                        return None
                return fd
        except Exception as exc:
            # log only the exception class name. ``str(exc)``
            # can include the lock file path (which contains the user's
            # home directory) — leaking it to stderr/debug logs is a
            # minor PII/privacy leak. The class name (e.g.
            # ``PermissionError``, ``OSError``) is enough for an
            # operator to diagnose the failure mode without exposing
            # the on-disk path layout.
            log.debug(
                "[LOG-SETUP] inter-process rotation lock acquire failed (%s); falling back to racy rotation",
                type(exc).__name__,
            )
            if isinstance(fd, int):
                with contextlib.suppress(OSError):
                    os.close(fd)
        return None

    def _release_rotation_lock(self, fd) -> None:
        if fd is None:
            return
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            elif os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                with contextlib.suppress(OSError):
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except Exception as exc:  # noqa: BLE001 — release-path: surface at DEBUG, never propagate during finally
            log.debug(
                "[LOG-SETUP] inter-process rotation lock release failed (%s)",
                type(exc).__name__,
                exc_info=True,
            )
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    def _rotation_needed(self) -> bool:
        """Re-check whether rotation is still needed after lock acquisition."""
        try:
            return os.path.getsize(self.baseFilename) >= self.maxBytes
        except OSError:
            return True

    def doRollover(self) -> None:  # noqa: D401, N802
        # tighten the process umask for the duration of the
        # rollover so the freshly-created active log file is 0o600 from
        # the very first ``open()`` call (mode 0o666 & ~umask=0o077 →
        # 0o600). Previously the umask had been restored to the parent's
        # value (typically 0o022 → 0o644 world-readable) by the time
        # ``doRollover`` ran, so ``super().doRollover()`` created the
        # file world-readable and the post-creation chmod ran AFTER the
        # rotation lock was released — a TOCTOU window during which a
        # co-located user could ``open()`` the file and read
        # dictated-text fragments. Tightening the umask here closes the
        # window at the source: the file is born 0o600. Restored in
        # ``finally`` so the umask change does not leak to other
        # threads or to subprocesses spawned after doRollover returns.
        #
        # Short-circuit on the file-size pre-check: if the active log
        # file is under the size cap, no rotation is needed. We do this
        # BEFORE acquiring the rotation lock so the no-op path
        # doesn't acquire + release the inter-process lock (which
        # would still show up in lock-acquisition telemetry and in
        # the regression test's call-order spy). The check is
        # duplicated AFTER lock acquisition in case another process
        # rotated while we were acquiring the lock.
        if not self._rotation_needed():
            return
        saved_umask = os.umask(0o077)
        lock_fd = self._acquire_rotation_lock()
        try:
            if not self._rotation_needed():
                return
            super().doRollover()
            # Belt-and-suspenders: chmod INSIDE the lock + try
            # block (before ``_release_rotation_lock``) so even if a
            # caller bypassed the umask (or a future refactor swapped
            # the open mode), the file is still re-locked to 0o600
            # before any other process can observe it. The umask
            # already ensures 0o600 at creation; this chmod guarantees
            # it post-hoc and runs in the same critical section that
            # holds the inter-process rotation lock.
            #
            # Logged (not silently suppressed) so an operator can see
            # when the post-rotation chmod fails — e.g. on NFS with
            # root-squash, on a read-only filesystem, or under a
            # SELinux policy that denies chmod. A silent suppress
            # would leave the freshly-rotated log file world-readable
            # (0o644) indefinitely with no signal that the privacy
            # guarantee had degraded. The WARNING is the only
            # operator-visible surface for this failure mode. Log
            # only the exception class name (not ``str(exc)``, which
            # can include the log file path → home-directory leak).
            if os.name == "posix":
                try:
                    os.chmod(self.baseFilename, 0o600)
                except OSError as exc:
                    log.warning(
                        "[LOG-SETUP] post-rotation chmod to 0o600 failed "
                        "(%s) — log file may be world-readable; investigate "
                        "filesystem perms (NFS root-squash, read-only mount, "
                        "SELinux policy)",
                        type(exc).__name__,
                    )
        finally:
            self._release_rotation_lock(lock_fd)
            os.umask(saved_umask)


def close_devnull_files() -> None:
    """Close all devnull file descriptors opened during :func:`setup_logging`.

    Called during application shutdown so the FDs don't leak.
    """
    for f in _devnull_files:
        with contextlib.suppress(Exception):
            f.close()
    _devnull_files.clear()


def register_devnull_file(fd) -> None:
    """Register a devnull file descriptor for cleanup on shutdown.

    Used by ``app.py`` when reopening stdout/stderr after a console
    close event (Windows).
    """
    _devnull_files.append(fd)
