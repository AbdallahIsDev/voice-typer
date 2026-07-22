"""Voice Typer — centralized logging infrastructure.

This module is the single source of truth for all logging configuration
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
  :class:`_correlation_id` — correlation-id context propagation (RW-13)
"""

from __future__ import annotations

import contextlib
import contextvars
import json
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

# RW-13: a per-context correlation id that flows through IPC dispatch and
# the dictation pipeline so that all log lines belonging to one user
# request / transcription cycle carry the same ``correlation_id``.  It is
# stored in a :class:`contextvars.ContextVar` so concurrent async/threaded
# work (multiple overlapping IPC requests, the transcription thread) each
# see their own value without explicit parameter threading on every call
# site.  ``""`` means "no correlation id in scope" — formatters render it
# as an absent key (JSON) / nothing (text).
_correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("voice_typer_correlation_id", default="")


def set_correlation_id(correlation_id: str) -> object:
    """Set the active correlation id for the current execution context.

    Call from IPC dispatch (request id) or the dictation pipeline
    (cycle id) so downstream logs auto-carry it.  Returns the token
    captured by :meth:`contextvars.ContextVar.set` — pass it to
    :func:`reset_correlation_id` (or use the :func:`correlation_id`
    context manager) to restore the previous value.
    """
    return _correlation_id_ctx.set(correlation_id)


def get_correlation_id() -> str:
    """Return the active correlation id (``""`` if none in scope)."""
    return _correlation_id_ctx.get()


def reset_correlation_id(token) -> None:
    """Restore a correlation id previously captured via :func:`set_correlation_id`."""
    _correlation_id_ctx.reset(token)


class _correlation_id:  # noqa: N801 — lowercase-by-design context manager
    """Context manager that sets a correlation id for its block.

    Usage::

        with log._correlation_id(cycle_id):
            ...  # all logs here carry correlation_id=cycle_id
    """

    def __init__(self, correlation_id: str) -> None:
        self._correlation_id = correlation_id
        self._token = None

    def __enter__(self) -> _correlation_id:
        if self._correlation_id:
            self._token = _correlation_id_ctx.set(self._correlation_id)
        return self

    def __exit__(self, *exc) -> None:
        if self._token is not None:
            _correlation_id_ctx.reset(self._token)
            self._token = None


def _json_logging_enabled() -> bool:
    """RW-13: structured JSON logging is opt-in via ``VOICE_TYPER_LOG_JSON``.

    Keeps the human-readable text format as the default so existing
    operator workflows (grep, tail) are unaffected.
    """
    return os.environ.get("VOICE_TYPER_LOG_JSON", "").lower() in ("1", "true", "yes")


_devnull_files: list = []
"""File descriptors opened for pythonw.exe stdio redirection."""


def reset() -> None:
    """Reset all logging state — called by tests to avoid cross-test contamination."""
    global _session_id
    _session_id = ""
    for f in _devnull_files:
        with contextlib.suppress(Exception):
            f.close()
    _devnull_files.clear()
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
    """

    _MARKER = "bubble_level"

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return self._MARKER not in msg


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
        "voice typer starting",
        "startup",
        "tray icon created",
        "tray event loop",
        "entering tray",
        "found microphone",
    ],
    "HOTKEY": [
        "hotkey",
        "register",
        "unregister",
        "polling",
        "getasynckeystate",
        "platform is win32",
        "key-down",
        "vk=0x",
    ],
    "DICTATION": [
        "dictation",
        "recording started",
        "recording stopped",
        "starting recording",
        "stopping recording",
    ],
    "RECORDING": [
        "microphone",
        "device query",
        "native rate",
        "resampl",
        "buffer telemetry",
        "audio captured",
        "silence",
        "chunk",
    ],
    "TRANSCRIBE": [
        "transcrib",
        "transcription thread",
        "transcription complete",
        "transcription failed",
        "clipboard",
        "paste",
    ],
    "SHUTDOWN": [
        "shutdown",
        "stopping",
        "stopped",
        "exiting",
        "tray icon stopped",
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
        return msg[1:close], msg[close + 1 :].lstrip()
    return None, msg


# ── Colour formatters ─────────────────────────────────────────────────


class _ColorFormatter(logging.Formatter):
    """ANSI-coloured formatter for stderr (terminal output).

    Design
    ------
    - Timestamp dimmed to recede visually
    - Per-process ``[session_id]`` bracket dimmed (SGR 2) so it is
      available for correlation but does not compete with the level
      colour or message body.
    - INFO level label omitted (redundant on ~every line)
    - WARN / ERR / FATAL full-line coloured with level label
    - Lines with ``[TOPIC]`` prefix coloured by topic
    - Lines without prefix infer topic from content keywords
    """

    _DIM = "38;5;242"  # grey
    _DIM_ATTR = "2"  # SGR 2 = faint/dim attribute (ECMA-48)
    # LOG-COLOR-FIX: WARN was 38;5;214 (orange #FFAF00) which
    # 256→16-color quantization on Windows conhost maps to bright-red,
    # making WARN look red and ERROR look yellow by comparison — the
    # inversion the user reported. Changed to 38;5;226 (pure yellow
    # #FFFF00) which quantizes to bright-yellow slot 14 on Windows
    # conhost, matching the standard WARN=yellow / ERROR=red convention.
    _LVL_COLOR = {
        logging.WARNING: "38;5;226",  # pure yellow (#FFFF00)
        logging.ERROR: "38;5;196",  # pure red (#FF0000)
        logging.CRITICAL: "38;5;196;1",  # red bold
    }
    _LVL_SYM = {
        logging.WARNING: "WARN",
        logging.ERROR: "ERR",
        logging.CRITICAL: "FATAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        if ts[0] == "0":
            ts = ts[1:]
        msg = record.getMessage()
        topic, _ = _extract_topic(msg)
        # G4-CR-12: the per-process ``[hex session_id]`` bracket is rendered
        # on every line so operators can correlate log entries across
        # process restarts and disambiguate interleaved lines from
        # concurrent backends.  The bracket is dimmed (SGR 2) so it
        # recedes visually and does not compete with the level colour or
        # message body.  When ``_SessionFilter`` has not yet run (early
        # startup, third-party loggers that bypass the voice_typer
        # logger) the bracket renders ``[--------]`` so the line still
        # has a well-formed correlation field.
        session_id = getattr(record, "session_id", "") or "--------"
        bracket = f"\033[{self._DIM_ATTR}m[{session_id}]\033[0m"

        if record.levelno >= logging.WARNING:
            c = self._LVL_COLOR.get(record.levelno, "0")
            sym = self._LVL_SYM.get(record.levelno, "????")
            # Full-line colour: emit colour, ts, dim bracket (SGR 22
            # restores normal intensity so the level symbol + message
            # stay in the level colour), then reset.
            return f"\033[{c}m{ts}  \033[{self._DIM_ATTR}m[{session_id}]\033[22m  {sym} {msg}\033[0m"

        # INFO — dim timestamp, dim session_id bracket, no level label,
        # message coloured by topic.
        prefix = f"\033[{self._DIM}m{ts}\033[0m"
        tc = _TOPIC_COLOR.get(topic) if topic else None
        if tc is None and not topic:
            inferred = _infer_topic(msg)
            if inferred:
                tc = _TOPIC_COLOR.get(inferred)
        body = f"\033[{tc}m{msg}\033[0m" if tc else msg
        return f"{prefix}  {bracket}  {body}"


class _FileFormatter(logging.Formatter):
    """Clean plain-text formatter for the ``voice-typer.log`` file.

    The file always contains clean, plain text without any ANSI escape
    codes.  If you need coloured log output, use the terminal stderr
    stream (which uses ``_ColorFormatter``).

    Format::

        2026-06-28 18:36:22  [a3f1b2c4]  [MainThread]  INFO   [voice_typer.server.app]  [HOTKEY] RegisterHotKey succeeded
        2026-06-28 18:36:22  [a3f1b2c4]  [MainThread]  WARN   [voice_typer.server.app]  [ENV] Invalid value ...
        2026-06-28 18:36:22  [a3f1b2c4]  [TranscribeThread]  ERROR  [voice_typer.server.dictation_pipeline]  [RECORDING] Stream finished unexpectedly

    Fields (left to right):

    - ``ts``                 — ``YYYY-MM-DD  HH:MM:SS`` timestamp
    - ``[session_id]``       — 8-char per-process hex ID rendered by
      ``_SessionFilter`` (``[--------]`` placeholder when the filter
      has not run, e.g. third-party loggers that bypass ``voice_typer``).
      Lets operators correlate log entries across process restarts and
      disambiguate interleaved lines from concurrent backends.
    - ``[threadName]``       — name of the emitting thread (always
      present; ``MainThread`` for the default case).
    - ``[taskName]``         — Python 3.12+ asyncio task name, emitted
      only when set (omitted for synchronous call sites).
    - ``label``              — level label (``DEBUG`` / ``INFO `` /
      ``WARN `` / ``ERROR`` / ``FATAL``), aligned to 5 chars.
    - ``[component]``        — module/logger name (``record.component``
      when ``_SessionFilter`` injected it, else ``record.name``).
    - ``msg``                — the redacted log message.

    Level labels are aligned so lines scroll cleanly:
    - ``DEBUG``   (5 chars)
    - ``INFO``    (4 chars)
    - ``WARN``    (4 chars)
    - ``ERROR``   (5 chars)
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
        ts = self.formatTime(record, "%Y-%m-%d  %H:%M:%S")
        msg = record.getMessage()
        label = self._LVL_LABEL.get(record.levelno, "INFO ")
        # G4-CR-12: render the per-process session_id bracket so operators
        # can correlate log lines across process restarts.  ``--------``
        # placeholder when ``_SessionFilter`` has not injected the attribute
        # (third-party loggers, early startup, unit-test records built by
        # hand) — empty brackets look like a bug.
        session_id = getattr(record, "session_id", "") or "--------"
        # G4-L-16: render the component (module/logger name) so operators
        # can tell which subsystem produced a line without parsing the
        # message text.  Defaults to ``record.name`` for records that
        # bypass ``_SessionFilter``.
        component = getattr(record, "component", record.name)
        # G4-L-15: render the emitting thread name so threaded pipelines
        # (transcription thread, prewarm pipeline, IPC workers) can be
        # distinguished in the log.  ``taskName`` (Python 3.12+) is
        # emitted inline when an asyncio task is in scope.
        thread_name = getattr(record, "threadName", "") or ""
        task_bracket = ""
        task_name = getattr(record, "taskName", None)
        if task_name:
            task_bracket = f"  [{task_name}]"
        return f"{ts}  [{session_id}]  [{thread_name}]{task_bracket}  {label}  [{component}]  {msg}"


class _JsonFormatter(logging.Formatter):
    """Structured JSON formatter (RW-13) — opt-in via ``VOICE_TYPER_LOG_JSON=1``.

    Emits one JSON object per line with a flat, stable schema so log
    aggregation tools can index and query fields directly instead of
    regex-matching free-text.  Schema::

        {
          "ts": "2026-07-15 12:34:56",
          "level": "INFO",
          "component": "voice_typer.server.recording",
          "session_id": "a3f1b2c4",     # 8-char per-process hex ID ("" if _SessionFilter has not run)
          "thread": "MainThread",        # name of the emitting thread
          "task": "transcribe-cycle",    # Python 3.12+ asyncio task name, present only when set
          "topic": "RECORDING",          # present only if a [TOPIC] prefix exists
          "correlation_id": "#7",         # present only when a correlation id is in scope
          "message": "Microphone opened (rate=16000)"
        }

    Design notes
    ------------
    - ``message`` is the *redacted* text: the PIIRedactionFilter mutates
      ``record.msg`` (and caches redacted ``record.exc_text``) before any
      formatter runs (the filter is attached to the handler), so the JSON
      output is already PII-scrubbed — the same guarantee as the text
      formatters.  No secret can reach the JSON line that couldn't reach
      the text line.
    - ``session_id`` is read from ``record.session_id`` (injected by
      ``_SessionFilter``) and is always present in the payload — empty
      string when the filter has not run, so aggregators can query
      ``session_id != ""`` to find correlated lines without
      ``KeyError``-prone ``.get()`` fallbacks.  G4-CR-13.
    - ``thread`` is the emitting thread name (always present).
      ``task`` is the Python 3.12+ asyncio task name, omitted entirely
      when not in scope (synchronous call sites) so the common case
      stays compact.  G4-L-15.
    - ``correlation_id`` is read from the :func:`get_correlation_id`
      contextvar, not from the record, so handlers that set it (IPC
      dispatch, dictation pipeline) don't need to thread it onto every
      ``log.info`` call.  It is omitted entirely when empty, keeping
      single-line records compact for the common (no-correlation) case.
    - ``topic`` is extracted from the ``[TOPIC]`` prefix when present;
      messages with no explicit prefix (and no inferred topic) simply
      omit it.  We deliberately do NOT run the keyword-inference used by
      the colour formatter — JSON consumers filter on ``component`` /
      structured fields, and guessing a topic from free text would add
      noise and be impossible to query consistently.
    - No ANSI escapes, ever.  Output is ``json.dumps`` with
      ``ensure_ascii=False`` so Unicode (e.g. transcriptions, non-ASCII
      device names) is preserved as readable UTF-8, and ``sort_keys`` is
      avoided so the field order above is stable.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%d  %H:%M:%S"),
            "level": record.levelname,
            "component": getattr(record, "component", record.name),
            # G4-CR-13: emit ``session_id`` so JSON aggregators can group
            # lines by process session (empty string when ``_SessionFilter``
            # has not run — present-but-empty keeps the schema flat).
            "session_id": getattr(record, "session_id", ""),
            # G4-L-15: emit the emitting thread name so threaded pipelines
            # (transcription thread, prewarm pipeline, IPC workers) can be
            # distinguished in structured consumers.
            "thread": getattr(record, "threadName", ""),
            "message": record.getMessage(),
        }
        # Python 3.12+ asyncio task name — omitted when not in scope so
        # synchronous call sites keep the payload compact.
        task_name = getattr(record, "taskName", None)
        if task_name:
            payload["task"] = task_name
        # Topic prefix (e.g. "[HOTKEY]") — purely structural convenience.
        topic, _ = _extract_topic(str(payload["message"]))
        if topic:
            payload["topic"] = topic
        # Correlation id from the execution context (IPC request id /
        # dictation cycle id).  Omitted when not in scope.
        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        return json.dumps(payload, ensure_ascii=False)


# ── One-time setup ────────────────────────────────────────────────────


def _apply_per_module_log_levels() -> None:
    """Apply per-module log level overrides from ``VOICE_TYPER_LOG_LEVEL_MODULES``.

    Format::

        VOICE_TYPER_LOG_LEVEL_MODULES="module.path=LEVEL,another.module=LEVEL"

    where ``LEVEL`` is a ``logging`` level name (``DEBUG``, ``INFO``,
    ``WARNING``, ``ERROR``, ``CRITICAL``).  Invalid entries are silently
    skipped (best-effort) so a typo in one entry does not break logging
    setup.  Lets operators crank up DEBUG on a single subsystem (e.g.
    ``voice_typer.server.dictation_pipeline``) without enabling DEBUG
    globally and flooding the rotating file with high-frequency events
    from unrelated subsystems.
    """
    raw = os.environ.get("VOICE_TYPER_LOG_LEVEL_MODULES", "")
    if not raw:
        return
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        name, _, level_str = entry.partition("=")
        name = name.strip()
        level_str = level_str.strip().upper()
        if not name or not level_str:
            continue
        level = getattr(logging, level_str, None)
        if not isinstance(level, int):
            continue
        logging.getLogger(name).setLevel(level)


def _ensure_last_resort_redacted(pii_filter: logging.Filter) -> None:
    """G4-M-27 (partial): ensure the global ``lastResort`` handler carries ``PIIRedactionFilter``.

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
    # Idempotent: skip if a PIIRedactionFilter is already attached.
    if any(type(f).__name__ == "PIIRedactionFilter" for f in last_resort.filters):
        return
    last_resort.addFilter(pii_filter)


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
        If ``True``, the stderr handler AND the rotating file handler
        emit DEBUG-level messages (G4-H-35).  When ``False`` both
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

    # G4-H-07: tighten the process umask to 0o077 while creating log
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

        # ── 3. Rotating file handler (PROD-016) ────────────────────────
        config_dir.mkdir(parents=True, exist_ok=True)
        # G4-H-07: lock down the config dir itself so co-located users
        # cannot ``cat`` the log file even if the per-file chmod is missed
        # (defence in depth — both this and the per-file chmod below are
        # best-effort on Windows where POSIX perms do not apply).
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(config_dir, 0o700)
        log_file = config_dir / "voice-typer.log"

        # RW-13: structured JSON logging is opt-in via VOICE_TYPER_LOG_JSON.
        # When enabled, the file (and console, below) use _JsonFormatter so
        # aggregation tools get one JSON object per line.  The PIIRedactionFilter
        # still runs first (attached below), so JSON output is redacted exactly
        # like the text output.  Human-readable text remains the default.
        json_mode = _json_logging_enabled()
        _file_formatter = _JsonFormatter() if json_mode else _FileFormatter()

        # HOTKEY-CRASH: use ``errors='backslashreplace'`` so Unicode
        # characters that can't be encoded in the system locale (cp1252
        # on Windows, e.g. → → right arrow) are escaped as \\uXXXX
        # instead of being silently replaced with the � replacement
        # character.  Without this, valuable diagnostic symbols like
        # arrows, em-dashes, and smart quotes become unreadable trash
        # in the log file.
        handler = logging.handlers.RotatingFileHandler(
            log_file,
            # ADR-0020 §11: 5 MiB per file, keep 5 backups (was 1 MiB × 2).
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
            errors="backslashreplace",
        )
        # G4-H-07: lock down the log file itself (0o600 — only the
        # owning user can read dictated-text previews, exception
        # tracebacks, and hotkey registrations).  Best-effort on POSIX;
        # silently no-op on Windows where the umask already enforced
        # 0o600 at creation time via the ``os.umask(0o077)`` above.
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(log_file, 0o600)
        # G4-H-35: gate the file handler on the ``debug`` flag so
        # production runs do not churn through 5 MiB × 5 of DEBUG noise.
        # Root stays at DEBUG so child loggers can still emit DEBUG
        # records when ``VOICE_TYPER_DEBUG=1`` is set — the handler
        # filter is what actually drops them at INFO level.
        handler.setLevel(logging.DEBUG if debug else logging.INFO)
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
        # RW-6: the filter is attached to BOTH the ``voice_typer`` logger
        # (so records logged directly to it are redacted before any handler
        # sees them) AND to each handler (so records logged to *child*
        # loggers like ``voice_typer.server.app`` — which do not trigger
        # the parent logger's filters per Python's logging semantics — are
        # also redacted).  Attaching to the handler is what makes the
        # redaction actually fire for the vast majority of call sites,
        # which use ``logging.getLogger(__name__)`` directly and therefore
        # log to ``voice_typer.server.<module>``.  The filter is idempotent
        # (redacting an already-redacted message is a no-op), so
        # double-filtering records that hit both the logger and handler
        # filters is harmless.
        from voice_typer.server.security import PIIRedactionFilter as _PIIRedactionFilter

        _pii_filter = _PIIRedactionFilter()
        handler.addFilter(_pii_filter)
        # a-review Finding 5: attach ``_SessionFilter`` to the file handler
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
        if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
            root.addHandler(handler)
        root.addFilter(_SessionFilter())
        root.addFilter(_pii_filter)

        root.setLevel(logging.DEBUG)

        # PROD-020: quiet mode for enterprise deployments
        if quiet:
            root.setLevel(logging.WARNING)

        # Per-module log level overrides (env: VOICE_TYPER_LOG_LEVEL_MODULES).
        # Applied AFTER root level set so they take precedence over the root
        # default — operators can crank DEBUG on a single subsystem without
        # enabling DEBUG globally.
        _apply_per_module_log_levels()

        # G4-M-27 (partial): ensure the global ``lastResort`` handler also
        # carries PIIRedactionFilter so third-party loggers (keyring,
        # urllib3, websockets) that bypass voice_typer's handlers do not
        # leak secrets via the fallback stderr path.
        _ensure_last_resort_redacted(_pii_filter)

        # ── 4. Fix stderr encoding for Unicode ─────────────────────────
        if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
            with contextlib.suppress(OSError):
                sys.stderr.reconfigure(errors="backslashreplace")

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
            # RW-13: in JSON mode the console also emits structured records
            # (no ANSI colouring — JSON consumers parse the line, not the
            # rendering).  The PII filter still runs below, so console JSON
            # output is redacted too.
            stream.setFormatter(_JsonFormatter() if json_mode else _ColorFormatter())
            # RW-6: attach the same PII / API-key redaction filter to the
            # console handler so secrets don't leak to the terminal either.
            stream.addFilter(_pii_filter)
            # a-review Finding 5: same reasoning as the file handler — attach
            # ``_SessionFilter`` to the stream handler too so console output
            # also carries the session_id bracket for records from child
            # loggers.
            stream.addFilter(_SessionFilter())
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
    finally:
        os.umask(_old_umask)


def get_log_file_path(config_dir: Path | None = None) -> Path:
    """Return the absolute path to ``voice-typer.log``.

    G4-L-19: used by agent 2-y for the in-app log viewer (``View Main
    Log`` button alongside ``Open Log Folder``).  Centralising the
    literal here means the viewer and ``setup_logging`` agree on the
    filename even if it ever changes.

    Parameters
    ----------
    config_dir:
        Optional override (e.g. tests pointing at ``tmp_path``).  When
        ``None``, the canonical config dir is resolved via
        :func:`voice_typer.server._paths.config_dir` (lazy import to
        avoid circular imports at module load time).

    Returns
    -------
    Path
        ``<config_dir>/voice-typer.log``.  The path may not yet exist
        on disk — callers should check ``.exists()`` before opening.
    """
    if config_dir is None:
        from voice_typer.server import _paths

        config_dir = _paths.config_dir()
    return config_dir / "voice-typer.log"


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
        with contextlib.suppress(Exception):
            # Best-effort: if the stream is closed or broken, swallow
            # so we don't mask the original log record.  (logging raises
            # the real exception via handleError; we only get here if
            # flush itself fails.)
            self.flush()


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
