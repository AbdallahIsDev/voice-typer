"""One-time logging setup, path helpers, migration, and retention sweep.

Extracted from the original monolithic ``log/__init__.py``. Contains:

- :data:`LOG_SUBDIR`, :func:`get_logs_dir`, :func:`get_log_file_path`
- Legacy-log migration (``_maybe_migrate_legacy_logs``)
- Session-start retention sweep (``_sweep_stale_logs``, Tiers 1+2)
- Per-module level overrides (``_apply_per_module_log_levels``,
  :func:`set_module_level`, :func:`get_module_levels`)
- Third-party logger silencing (``_apply_third_party_logger_levels``)
- :func:`setup_logging` — the one-time file + console configuration

``setup_logging`` resolves :func:`_sweep_stale_logs` and
:class:`~voice_typer.server.log.handlers._SecureTruncatingFileHandler`
via the package object at call time so tests that
``monkeypatch.setattr(voice_typer.server.log, ...)`` keep working after
the split (C-ARCH-2 sibling-module late lookup).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path

# Centralized log-retention constants.  Mirror the Rust-side
from voice_typer.server._log_constants import (
    LOG_AGE_RETENTION_SECONDS,
    LOG_MAX_BYTES,
    LOG_SIZE_FALLBACK_BYTES,
)
from voice_typer.server.log import state as _state
from voice_typer.server.log.formatters import (
    _ColorFormatter,
    _FileFormatter,
    _JsonFormatter,
)
from voice_typer.server.log.handlers import (
    _BubbleLevelExclusionFilter,
    _FlushingStreamHandler,
    _SessionFilter,
)

# Keep the historical logger name so ``caplog.at_level(...,
log = logging.getLogger("voice_typer.server.log")

# All log files live under a ``logs/`` subdirectory of the config dir
LOG_SUBDIR = "logs"

# Legacy pre-O1 log files that once lived directly in the config dir.
_LEGACY_LOG_NAMES: tuple[str, ...] = (
    "lausu.log",
    "prewarm.log",
    "worker.log",
    "startup-error.log",
    "lausu-crash-buffer.log",
)
_LEGACY_LOG_GLOBS: tuple[str, ...] = (
    "lausu.log.*",  # legacy main-process rotations
    "prewarm.log.*",  # legacy prewarm rotations
    "lausu-prewarm.log.*",  # legacy prewarm rotations (file no longer created)
)


def get_logs_dir(config_dir: Path) -> Path:
    """Return the directory that holds all log files."""
    return Path(config_dir) / LOG_SUBDIR


def _maybe_migrate_legacy_logs(config_dir: Path) -> None:
    """Move pre-``logs/`` log files from the config-dir root into ``logs/``."""
    try:
        src_root = Path(config_dir)
        dst_root = get_logs_dir(config_dir)
        if not src_root.is_dir():
            return
        for name in _LEGACY_LOG_NAMES:
            _maybe_move_legacy_log_file(src_root, dst_root, name)
        for pattern in _LEGACY_LOG_GLOBS:
            for src in src_root.glob(pattern):
                if not src.is_file() or src.name.endswith(".lock"):
                    continue
                _maybe_move_legacy_log_file(src_root, dst_root, src.name)
    except Exception as exc:  # noqa: BLE001, best-effort migration
        log.debug("[LOG-SETUP] legacy log migration failed: %s", exc)


def _maybe_move_legacy_log_file(src_root: Path, dst_root: Path, name: str) -> None:
    """Move one legacy log file from ``src_root`` to ``dst_root`` if safe."""
    try:
        src = src_root / name
        dst = dst_root / name
        if not src.is_file() or dst.exists():
            return
        dst_root.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        log.info("[LOG-SETUP] migrated legacy log file %s -> %s", src, dst)
    except Exception as exc:  # noqa: BLE001, best-effort migration
        log.debug("[LOG-SETUP] legacy log migration skipped %s: %s", name, exc)


def _sweep_stale_logs(config_dir: Path) -> None:
    """Delete stale log files at session start (Tiers 1 + 2).

    Three-tier cleanup design: this function implements Tiers 1 and 2
    (the session-start sweeps); Tier 3 (the mid-session hard ceiling)
    lives in the ``_SecureTruncatingFileHandler`` rollover path:

      * **Tier 1, age (primary):** any log file in ``logs/`` whose last
        write is older than ``LOG_AGE_RETENTION_SECONDS`` (7 days) is
        deleted. Bounds storage for low-traffic installs whose logs
        would otherwise sit forever.

      * **Tier 2, size fallback:** any log file larger than
        ``LOG_SIZE_FALLBACK_BYTES`` (25 MB) is deleted even if freshly
        written, covers a marathon session that pushed a log past the
        fallback between startups. Checked ONLY here (session start),
        never mid-session.

    Runs at the TOP of :func:`setup_logging`: BEFORE the rotating file
     handler opens ``lausu.log``, so the active file itself can be
    deleted when stale/oversized and a fresh one is created for the new
    session ("cleans everything up and starts fresh").

    Scope: every regular file in ``logs/`` EXCEPT the inter-process
    truncation lock files (``*.lock``), they must persist across setups
    so the next process can acquire the flock. This covers Python-owned
    logs (``lausu.log``, ``worker.log``, ``prewarm.log``,
    ``startup-error.log``, ``lausu-crash-buffer.log``) AND the
    host-owned logs (``lausu-rust.log`` + rotations, plus any
    legacy host log files still on disk). Files locked by another live
    process (e.g. the Rust host's logs in dev mode, where the host
    started first) fail the unlink, skipped silently; their owner
    sweeps them at its own startup (mirrored in
    ``src-tauri/src/platform/logging.rs``).

    Best-effort, any error is logged at DEBUG and swallowed so a single
    unreadable file does not abort the sweep or ``setup_logging``.
    Idempotent if called multiple times.
    """
    try:
        root = get_logs_dir(config_dir)
        if not root.is_dir():
            return
        now = time.time()
        for f in root.iterdir():
            # Skip directories and the inter-process truncation lock
            if not f.is_file() or f.name.endswith(".lock"):
                continue
            try:
                stat = f.stat()
            except OSError:
                continue
            age = now - stat.st_mtime
            oversized = stat.st_size > LOG_SIZE_FALLBACK_BYTES
            if age <= LOG_AGE_RETENTION_SECONDS and not oversized:
                continue
            reason = f"age={age / 86400:.1f}d" if age > LOG_AGE_RETENTION_SECONDS else ""
            if oversized:
                size_mb = stat.st_size / (1024 * 1024)
                reason = f"{reason}{'+' if reason else ''}size={size_mb:.1f}MB"
            try:
                f.unlink()
                log.debug(
                    "[LOG-SETUP] purged stale log %s (%s)",
                    f.name,
                    reason,
                )
            except OSError as exc:
                # Locked by another live process (host-first launch
                log.debug(
                    "[LOG-SETUP] failed to purge stale log %s: %s",
                    f.name,
                    exc,
                )
    except Exception as exc:  # noqa: BLE001, best-effort sweep
        log.debug("[LOG-SETUP] stale-log sweep failed: %s", exc)


def get_log_file_path(config_dir: Path | None = None, *, process_name: str = "main") -> Path:
    """Return the absolute path to the log file for the given process.

    used by agent 2-y for the in-app log viewer (``View Main
    Log`` button alongside ``Open Log Folder``).  Centralising the
    literal here means the viewer and ``setup_logging`` agree on the
    filename even if it ever changes.

    The ``process_name`` parameter routes each long-lived process to
    its OWN file so concurrent writers never share a file descriptor
    on the same file (which would race on the
    :class:`_SecureTruncatingFileHandler`'s in-place truncation
    rotation: see ``tests/test_log_multiprocess.py`` for
    the failure mode).

    Routing table:

    - ``"main"`` (default) and any unrecognised value → ``lausu.log``
    - ``"prewarm"`` → ``prewarm.log``
    - ``"worker"`` → ``worker.log`` (the runtime-pack WebSocket worker
      spawned by the Tauri host; without this case it would fall
      through to ``lausu.log`` and race the slim-core sidecar's
      rotation, the same race that motivated the ``prewarm`` case).

    Parameters
    ----------
    config_dir:
        Optional override (e.g. tests pointing at ``tmp_path``).  When
        ``None``, the canonical config dir is resolved via
        :func:`voice_typer.server._paths.config_dir` (lazy import to
        avoid circular imports at module load time).
    process_name:
        ``"main"`` (default), ``"prewarm"``, or ``"worker"``. Controls
        which log file is returned.  An unrecognised value falls back
        to the main log path (defensive: see
        ``test_get_log_file_path_unknown_process_name_falls_back_to_main``).

    Returns
    -------
    Path
        ``<config_dir>/logs/lausu.log`` / ``<config_dir>/logs/prewarm.log`` /
        ``<config_dir>/logs/worker.log``.  The path may not yet exist on disk —
        callers should check ``.exists()`` before opening.
    """
    if config_dir is None:
        from voice_typer.server import _paths

        config_dir = _paths.config_dir()
    logs_dir = get_logs_dir(config_dir)
    if process_name == "prewarm":
        # Single-file policy: the prewarm process writes to ONE file —
        return logs_dir / "prewarm.log"
    if process_name == "worker":
        # Single-file policy: the runtime-pack WebSocket worker
        return logs_dir / "worker.log"
    return logs_dir / "lausu.log"


def _json_logging_enabled() -> bool:
    """structured JSON logging is opt-in via ``VOICE_TYPER_LOG_JSON``."""
    return os.environ.get("VOICE_TYPER_LOG_JSON", "").lower() in ("1", "true", "yes")


def _apply_per_module_log_levels() -> None:
    """Apply per-module log level overrides from ``VOICE_TYPER_LOG_LEVEL_MODULES``.

    Format::

        VOICE_TYPER_LOG_LEVEL_MODULES="module.path=LEVEL,another.module=LEVEL"

    where ``LEVEL`` is a ``logging`` level name (``DEBUG``, ``INFO``,
    ``WARNING``, ``ERROR``, ``CRITICAL``).  Invalid entries are
    skipped (best-effort) so a typo in one entry does not break
    logging setup, but each skipped entry now logs a WARNING
    so the operator can see *which* entry was ignored and why, a
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
                "entry %r (reason: unknown level %r, expected DEBUG/INFO/WARNING/ERROR/CRITICAL)",
                entry,
                level_str,
            )
            continue
        logging.getLogger(name).setLevel(level)
        # record the override so get_module_levels can report it.
        _state._module_level_overrides[name] = level_str
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
        ``"CRITICAL"``), case-insensitive.  Invalid names raise
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
    _state._module_level_overrides[name] = level_str
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
    return dict(_state._module_level_overrides)


def _ensure_last_resort_redacted(pii_filter: logging.Filter) -> None:
    """Ensure the global ``lastResort`` handler carries ``PIIRedactionFilter``."""
    last_resort = getattr(logging, "lastResort", None)
    if last_resort is None:
        return
    # Idempotent: skip if a PIIRedactionFilter of the same class is
    if any(isinstance(f, type(pii_filter)) for f in last_resort.filters):
        return
    last_resort.addFilter(pii_filter)


# Third-party loggers the app depends on (directly or transitively)
_THIRD_PARTY_LOGGER_LEVELS: dict[str, int] = {
    "urllib3": logging.WARNING,
    "urllib3.connectionpool": logging.WARNING,
    "requests": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "websockets": logging.WARNING,
    "keyring": logging.WARNING,
    "sounddevice": logging.WARNING,
    "PIL": logging.WARNING,
    "numpy": logging.WARNING,
    "torch": logging.WARNING,
    "onnxruntime": logging.WARNING,
    "faster_whisper": logging.WARNING,
    "ctranslate2": logging.WARNING,
    "huggingface_hub": logging.WARNING,
    "transformers": logging.WARNING,
    "pystray": logging.WARNING,
    "asyncio": logging.WARNING,
}


def _apply_third_party_logger_levels() -> None:
    """Pin every logger in :data:`_THIRD_PARTY_LOGGER_LEVELS` to WARNING."""
    for name, level in _THIRD_PARTY_LOGGER_LEVELS.items():
        lib_logger = logging.getLogger(name)
        lib_logger.setLevel(level)
        lib_logger.handlers.clear()
        lib_logger.propagate = True


def setup_logging(
    config_dir: Path,
    *,
    debug: bool = False,
    quiet: bool = False,
    port_mode: bool = False,
    process_name: str = "main",
) -> str:
    """Configure Lausu logging, rotating file + optional coloured console.

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
        Accepted for backwards compatibility. NO LONGER forces coloured
        stderr output: ANSI colours are gated on
        ``sys.stderr.isatty()`` so ``--port`` runs whose stderr is
        redirected to a file stay plain and grep-friendly, while a
        terminal ``--port`` run still gets colours (a terminal IS a
        TTY, so the old ``or port_mode`` was redundant for the case it
        was designed for).
    process_name:
        Routes the rotating file handler to a per-process file so
        concurrent processes don't race on the same file.  ``"main"``
        (default) → ``lausu.log``; ``"prewarm"`` → ``prewarm.log``;
        ``"worker"`` → ``worker.log``.  The runtime-pack worker
        (``voice_typer/worker/__main__.py``) passes ``"worker"`` so it
        doesn't share a file descriptor with the slim-core sidecar
        (both writing to ``lausu.log`` would race on the
        ``_SecureTruncatingFileHandler``'s in-place truncation
        rotation).  An unrecognised value falls back to
        ``lausu.log``.

    Returns
    -------
    The 8-character hex session ID for this process.
    """
    # C-ARCH-2: resolve patchable collaborators via the public package
    import voice_typer.server.log as _log_pkg

    # tighten the process umask to 0o077 while creating log
    _old_umask = os.umask(0o077)
    try:
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")  # noqa: SIM115, must outlive setup_logging()
            _state._devnull_files.append(sys.stderr)
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")  # noqa: SIM115, must outlive setup_logging()
            _state._devnull_files.append(sys.stdout)
        if sys.stdin is None:
            sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115, must outlive setup_logging()
            _state._devnull_files.append(sys.stdin)

        # When spawned by the Rust Tauri host, accept the host's
        _host_session_id = os.environ.get("VOICE_TYPER_SESSION_ID", "")
        if _host_session_id and re.fullmatch(r"[0-9a-f]{8}", _host_session_id):
            _session_id = _host_session_id
        else:
            _session_id = uuid.uuid4().hex[:8]
        # Canonical store is the package attribute (tests snapshot/
        _log_pkg.__dict__["_session_id"] = _session_id

        config_dir.mkdir(parents=True, exist_ok=True)
        # lock down the config dir itself so co-located users
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(config_dir, 0o700)
        # All log files (main / prewarm / worker / crash buffer /
        logs_dir = get_logs_dir(config_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(logs_dir, 0o700)
        _maybe_migrate_legacy_logs(config_dir)
        # MUST run BEFORE the file handler below opens
        _log_pkg._sweep_stale_logs(config_dir)
        # Single-file policy: process_name routes each long-lived
        log_file = get_log_file_path(config_dir, process_name=process_name)

        # structured JSON logging is opt-in via VOICE_TYPER_LOG_JSON.
        json_mode = _json_logging_enabled()
        _file_formatter = _JsonFormatter() if json_mode else _FileFormatter()

        # use ``errors='backslashreplace'`` so Unicode
        handler = _log_pkg._SecureTruncatingFileHandler(
            log_file,
            # Single-file policy: ZERO backups.  When the file exceeds
            maxBytes=LOG_MAX_BYTES,
            backupCount=0,
            encoding="utf-8",
            errors="backslashreplace",
        )
        # lock down the log file itself (0o600, only the
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(log_file, 0o600)
        # gate the file handler on the ``debug`` flag so
        handler.setLevel(logging.WARNING if quiet else (logging.DEBUG if debug else logging.INFO))
        # ADR-0020 §11: keep high-frequency ``bubble_level`` events out of
        handler.addFilter(_BubbleLevelExclusionFilter())
        handler.setFormatter(_file_formatter)

        # PII / API-key redaction, imported lazily to avoid circular imports
        from voice_typer.server.security import PIIRedactionFilter as _PIIRedactionFilter

        _pii_filter = _PIIRedactionFilter()
        handler.addFilter(_pii_filter)
        # Attach ``_SessionFilter`` to the file handler
        _session_filter = _SessionFilter()
        handler.addFilter(_session_filter)

        root = logging.getLogger("voice_typer")
        # Avoid duplicate handlers if setup is called multiple times.
        _new_file_level = handler.level
        _new_file_formatter = handler.formatter
        for _existing in root.handlers:
            if isinstance(_existing, _log_pkg._SecureTruncatingFileHandler):
                _existing.setLevel(_new_file_level)
                if _new_file_formatter is not None:
                    _existing.setFormatter(_new_file_formatter)
        if not any(isinstance(h, _log_pkg._SecureTruncatingFileHandler) for h in root.handlers):
            root.addHandler(handler)
        # PII + session filters are attached to each HANDLER

        root.setLevel(logging.DEBUG)

        # quiet mode for enterprise deployments
        if quiet:
            root.setLevel(logging.WARNING)

        # Per-module log level overrides (env: VOICE_TYPER_LOG_LEVEL_MODULES).
        _apply_per_module_log_levels()

        # Silence noisy third-party loggers (urllib3 / websockets /
        _apply_third_party_logger_levels()

        # Ensure the global ``lastResort`` handler also
        _ensure_last_resort_redacted(_pii_filter)

        # ``line_buffering=True`` flushes on every newline, so each log
        if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
            with contextlib.suppress(OSError):
                sys.stderr.reconfigure(errors="backslashreplace", line_buffering=True)
        if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
            with contextlib.suppress(OSError):
                sys.stdout.reconfigure(errors="backslashreplace", line_buffering=True)

        # always flush after each emit so terminal log lines appear
        do_color = bool(sys.stderr is not None and sys.stderr.isatty())
        if sys.stderr is not None:
            stream = _FlushingStreamHandler()
            stream.setLevel(logging.DEBUG if debug else logging.INFO)
            if do_color:
                # in JSON mode the console also emits structured
                stream.setFormatter(_JsonFormatter() if json_mode else _ColorFormatter())
            else:
                # Non-TTY (Tauri sidecar, piped stderr, log redirection):
                stream.setFormatter(_JsonFormatter() if json_mode else _FileFormatter())
            # attach the same PII / API-key redaction filter to the
            stream.addFilter(_pii_filter)
            # Same reasoning as the file handler, attach
            stream.addFilter(_SessionFilter())
            # Avoid duplicate StreamHandlers if setup is called multiple times.
            _new_stream_level = stream.level
            _new_stream_formatter = stream.formatter
            for _existing in root.handlers:
                if isinstance(_existing, _FlushingStreamHandler):
                    _existing.setLevel(_new_stream_level)
                    if _new_stream_formatter is not None:
                        _existing.setFormatter(_new_stream_formatter)
            if not any(isinstance(h, _FlushingStreamHandler) for h in root.handlers):
                root.addHandler(stream)

        return _session_id
    finally:
        os.umask(_old_umask)
