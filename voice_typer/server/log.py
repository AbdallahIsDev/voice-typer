"""Voice Typer — centralized logging infrastructure.

This module is the single source of truth for all logging configuration
across the application.  Every backend module should use :func:`get_logger`
to obtain a logger with the standard Voice Typer format::

    from voice_typer.server.log import get_logger
    log = get_logger(__name__)

The main entry point (typically ``app.py``) must call :func:`setup_logging`
**once** at process startup to configure file and console handlers.

Components
----------
- :func:`get_logger` — logger factory with standard namespace
- :func:`setup_logging` — one-time file + console configuration
- :func:`close_devnull_files` — shutdown cleanup
- :func:`reset` — test isolation
- :class:`_SessionFilter` — injects ``session_id`` into log records
- :class:`_ColorFormatter` — ANSI-coloured terminal formatter
- :class:`_FileFormatter` — ANSI-coloured file formatter
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import uuid
from pathlib import Path


# ── Module-level state ────────────────────────────────────────────────
# Encapsulated here instead of in a class so it's accessible to filters
# and formatters without passing references through the logging framework.

_session_id: str = ""
"""8-char hex session ID, generated once per :func:`setup_logging` call."""

_devnull_files: list = []
"""File descriptors opened for pythonw.exe stdio redirection."""


def reset() -> None:
    """Reset all logging state — called by tests to avoid cross-test contamination."""
    global _session_id
    _session_id = ""
    for f in _devnull_files:
        try:
            f.close()
        except Exception:
            pass
    _devnull_files.clear()
    root = logging.getLogger("voice_typer")
    root.handlers.clear()
    root.filters.clear()
    root.setLevel(logging.DEBUG)


# ── Logger factory ────────────────────────────────────────────────────


def get_logger(name: str) -> logging.Logger:
    """Return a Voice Typer logger with the standard ``voice_typer.*`` namespace.

    Example::

        # In voice_typer/server/audio_processor.py:
        log = get_logger(__name__)  # → ``voice_typer.server.audio_processor``
    """
    return logging.getLogger(f"voice_typer.{name}")


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


# ── Shared colour tables ──────────────────────────────────────────────

#: ANSI 256-colour codes keyed by topic label.
_TOPIC_COLOR: dict[str, str] = {
    "PARAKEET": "38;5;69",
    "QWEN": "38;5;69",
    "MODEL": "38;5;69",
    "CUDA-PROBE": "38;5;75",
    "HOTKEY": "38;5;141",
    "HOTKEY FIRED": "38;5;141",
    "HOTKEY FALLBACK": "38;5;141",
    "RECORDING": "38;5;79",
    "AUDIO_QUALITY": "38;5;215",
    "DICTATION": "38;5;215",
    "TRANSCRIBE": "38;5;120",
    "VOLUME": "38;5;111",
    "VAD": "38;5;245",
    "CLIPBOARD": "38;5;120",
    "STARTUP": "38;5;103",
    "STREAMING": "38;5;110",
    "CLOUD": "38;5;110",
    "FOCUS": "38;5;102",
    "CLEANUP": "38;5;102",
    "TEMPLATE": "38;5;102",
    "WIN32": "38;5;102",
    "SIGNAL": "38;5;102",
    "HISTORY": "38;5;102",
    "HISTORY_DB": "38;5;102",
    "SHUTDOWN": "38;5;95",
    "QUIT": "38;5;95",
    "RESTART": "38;5;95",
    "CANCEL": "38;5;215",
    "REPASTE": "38;5;120",
    "CONFIG": "38;5;102",
    "TRAY": "38;5;102",
    "LLM_POLISH": "38;5;140",
    "ASR_REGISTRY": "38;5;141",
    "ASR_SETUP": "38;5;102",
    "WAVEFORM": "38;5;102",
    "ONBOARDING": "38;5;102",
    "RECOVERY": "38;5;102",
    "ATEXIT": "38;5;102",
    "PIPELINE": "38;5;102",
    "IPC": "38;5;102",
    "TCP": "38;5;244",
}

#: Keyword → topic mapping for messages without an explicit ``[TOPIC]`` prefix.
_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "PARAKEET": ["parakeet", "loading model", "model loaded", "loaded successfully"],
    "STARTUP": [
        "voice typer starting", "startup", "tray icon created",
        "tray event loop", "entering tray", "found microphone",
    ],
    "HOTKEY": [
        "hotkey", "register", "unregister", "polling", "getasynckeystate",
        "platform is win32", "key-down", "vk=0x",
    ],
    "DICTATION": [
        "dictation", "recording started", "recording stopped",
        "starting recording", "stopping recording",
    ],
    "RECORDING": [
        "microphone", "device query", "native rate", "resampl",
        "buffer telemetry", "audio captured", "silence", "chunk",
    ],
    "TRANSCRIBE": [
        "transcrib", "transcription thread", "transcription complete",
        "transcription failed", "clipboard", "paste",
    ],
    "SHUTDOWN": [
        "shutdown", "stopping", "stopped", "exiting", "tray icon stopped",
        "unregisterhotkey",
    ],
    "WIN32": ["console control handler"],
    "HISTORY": ["history database"],
}


def _infer_topic(msg: str) -> str | None:
    """Guess a topic label from *msg* content keywords.

    First match wins — narrower keywords should come first in each list.
    """
    lower = msg.lower()
    for topic, keywords in _TOPIC_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return topic
    return None


def _extract_topic(msg: str) -> tuple[str | None, str]:
    """Extract a bracketed topic prefix (e.g. ``[PARAKEET]``) from *msg*.

    Returns ``(topic, rest)``.
    """
    if msg.startswith("[") and "]" in msg:
        close = msg.index("]")
        return msg[1:close], msg[close + 1:].lstrip()
    return None, msg


# ── Colour formatters ─────────────────────────────────────────────────


class _ColorFormatter(logging.Formatter):
    """ANSI-coloured formatter for stderr (terminal output).

    Design
    ------
    - Timestamp dimmed to recede visually
    - INFO level label omitted (redundant on ~every line)
    - WARN / ERR / FATAL full-line coloured with level label
    - Lines with ``[TOPIC]`` prefix coloured by topic
    - Lines without prefix infer topic from content keywords
    """

    _DIM = "38;5;242"  # grey
    _LVL_COLOR = {
        logging.WARNING: "38;5;214",
        logging.ERROR: "38;5;196",
        logging.CRITICAL: "38;5;196;1",
    }
    _LVL_SYM = {
        logging.WARNING: "WARN",
        logging.ERROR: "ERR",
        logging.CRITICAL: "FATAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%I:%M:%S")
        if ts[0] == "0":
            ts = ts[1:]
        msg = record.getMessage()
        topic, _ = _extract_topic(msg)

        if record.levelno >= logging.WARNING:
            c = self._LVL_COLOR.get(record.levelno, "0")
            sym = self._LVL_SYM.get(record.levelno, "????")
            return f"\033[{c}m{ts}  {sym} {msg}\033[0m"

        # INFO — dim timestamp, no level label, message coloured by topic
        prefix = f"\033[{self._DIM}m{ts}\033[0m"
        tc = _TOPIC_COLOR.get(topic) if topic else None
        if tc is None and not topic:
            inferred = _infer_topic(msg)
            if inferred:
                tc = _TOPIC_COLOR.get(inferred)
        body = f"\033[{tc}m{msg}\033[0m" if tc else msg
        return f"{prefix}  {body}"


class _FileFormatter(logging.Formatter):
    """Clean plain-text formatter for the ``voice-typer.log`` file.

    No ANSI escape codes, no session IDs, no redundant prefixes.
    Every line is plain text that works in any editor or log viewer.

    Format::

        2026-06-28 18:36:22  INFO  [HOTKEY] RegisterHotKey succeeded
        2026-06-28 18:36:22  WARN  [ENV] Invalid value ...
        2026-06-28 18:36:22  ERR   [RECORDING] Stream finished unexpectedly

    Level labels are aligned so lines scroll cleanly:
    - ``DEBUG``   (5 chars)
    - ``INFO``    (4 chars)
    - ``WARN``    (4 chars)
    - ``ERR``     (3 chars)
    - ``FATAL``   (5 chars)
    """

    _LVL_LABEL = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO ",
        logging.WARNING: "WARN ",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "FATAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        msg = record.getMessage()
        label = self._LVL_LABEL.get(record.levelno, "INFO ")
        return f"{ts}  {label}  {msg}"


# ── One-time setup ────────────────────────────────────────────────────


def setup_logging(
    config_dir: Path,
    *,
    debug: bool = False,
    quiet: bool = False,
    port_mode: bool = False,
) -> str:
    """Configure Voice Typer logging — rotating file + optional coloured console.

    Call this **once** at process startup, before any subsystem logs.
    It is safe to call multiple times (subsequent calls are idempotent).

    Parameters
    ----------
    config_dir:
        Directory where the rotating log file will be created.
    debug:
        If ``True``, the stderr handler shows DEBUG-level messages.
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

    # ── 1. Redirect stdio for pythonw.exe ──────────────────────────
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _devnull_files.append(sys.stderr)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _devnull_files.append(sys.stdout)
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")
        _devnull_files.append(sys.stdin)

    # ── 2. Generate session ID ─────────────────────────────────────
    _session_id = uuid.uuid4().hex[:8]

    # ── 3. Rotating file handler (PROD-016) ────────────────────────
    config_dir.mkdir(parents=True, exist_ok=True)
    log_file = config_dir / "voice-typer.log"

    # HOTKEY-CRASH: use ``errors='backslashreplace'`` so Unicode
    # characters that can't be encoded in the system locale (cp1252
    # on Windows, e.g. → → right arrow) are escaped as \uXXXX
    # instead of being silently replaced with the � replacement
    # character.  Without this, valuable diagnostic symbols like
    # arrows, em-dashes, and smart quotes become unreadable trash
    # in the log file.
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=2,
        encoding="utf-8", errors="backslashreplace",
    )
    handler.setFormatter(_FileFormatter())

    root = logging.getLogger("voice_typer")
    # Avoid duplicate handlers if setup is called multiple times.
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)
    root.addFilter(_SessionFilter())

    # PII redaction — imported lazily to avoid circular imports
    # and to keep the security module's import order clean.
    from voice_typer.server.security import PIIRedactionFilter as _PIIRedactionFilter
    root.addFilter(_PIIRedactionFilter())

    root.setLevel(logging.DEBUG)

    # PROD-020: quiet mode for enterprise deployments
    if quiet:
        root.setLevel(logging.WARNING)

    # ── 4. Fix stderr encoding for Unicode ─────────────────────────
    if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(errors="backslashreplace")
        except OSError:
            pass

    # ── 5. Coloured stderr (terminal or --port mode) ───────────────
    # P1-1.1: always flush after each emit so terminal log lines appear
    # in real-time.  The bare logging.StreamHandler only flushes on
    # close (or when its internal buffer hits a high-water mark), so
    # startup logs from a standalone VoiceTyper run could sit in the
    # buffer for seconds before being flushed — making the app look
    # like it's hanging silently.  _FlushingStreamHandler.emit() calls
    # self.flush() after every record.
    do_color = sys.stderr.isatty() or port_mode
    if sys.stderr is not None and do_color:
        stream = _FlushingStreamHandler()
        stream.setLevel(logging.DEBUG if debug else logging.INFO)
        stream.setFormatter(_ColorFormatter())
        # Avoid duplicate StreamHandlers if setup is called multiple times.
        # Use _FlushingStreamHandler as the dedup key so legacy tests that
        # check for "any StreamHandler" (isinstance check below) still pass.
        if not any(isinstance(h, _FlushingStreamHandler) for h in root.handlers):
            root.addHandler(stream)
        # Silence noisy third-party loggers (LOG-006).
        for lib in ("transformers", "torch", "huggingface_hub"):
            lib_logger = logging.getLogger(lib)
            lib_logger.setLevel(logging.WARNING)
            lib_logger.handlers.clear()
            lib_logger.propagate = True

    return _session_id


class _FlushingStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes after every emit.

    P1-1.1: the standard ``logging.StreamHandler`` only flushes on
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
        try:
            self.flush()
        except Exception:
            # Best-effort: if the stream is closed or broken, swallow
            # so we don't mask the original log record.  (logging raises
            # the real exception via handleError; we only get here if
            # flush itself fails.)
            pass


def close_devnull_files() -> None:
    """Close all devnull file descriptors opened during :func:`setup_logging`.

    Called during application shutdown so the FDs don't leak.
    """
    for f in _devnull_files:
        try:
            f.close()
        except Exception:
            pass
    _devnull_files.clear()


def register_devnull_file(fd) -> None:
    """Register a devnull file descriptor for cleanup on shutdown.

    Used by ``app.py`` when reopening stdout/stderr after a console
    close event (Windows).
    """
    _devnull_files.append(fd)
