"""Logging formatters for the Voice Typer logging framework.

Extracted from the original monolithic ``log.py`` (logging-package
split). Contains:

- :func:`_iso_timestamp` — ISO 8601 timestamp with milliseconds + tz
- :data:`_TOPIC_COLOR` / :data:`_TOPIC_KEYWORDS` — colour tables
- :func:`_infer_topic` / :func:`_extract_topic` — topic-prefix helpers
- :func:`_append_exception_text` — shared traceback-appending helper
- :class:`_ColorFormatter` — ANSI-coloured terminal formatter (default)
- :class:`_FileFormatter` — plain-text file formatter (default)
- :class:`_JsonFormatter` — structured JSON formatter (opt-in,
  ``VOICE_TYPER_LOG_JSON=1``)

The formatters depend on :func:`get_correlation_id` (in
:mod:`voice_typer.server.log.correlation`) for the JSON correlation-id
field. Importing it from the sibling ``correlation`` module (rather
than from the parent ``log`` package) avoids a circular import.
"""

from __future__ import annotations

import json
import logging
import time

from voice_typer.server.log.correlation import get_correlation_id


def _iso_timestamp(
    record: logging.LogRecord,
    *,
    utc: bool = False,
    include_date: bool = True,
) -> str:
    """Return a clean timestamp.

    Text output (the default) is a clean, space-separated local
    timestamp with seconds precision — ``2026-08-08  22:18:59`` — with
    TWO spaces between the date and the time, no millisecond fraction,
    no ``T`` separator, and no timezone offset, so it reads naturally
    in the file and on the terminal.  The terminal formatter passes
    ``include_date=False`` to get the time only (``22:18:59``) — the
    date is deliberately kept out of console output.

    Pass ``utc=True`` for the JSON formatter, which emits a
    Z-suffixed UTC timestamp with millis (``2026-08-08T22:18:59.172Z``)
    that log aggregators expect — that path is unchanged and keeps the
    millisecond fraction + date.
    """
    if utc:
        ct = time.gmtime(record.created)
        base = time.strftime("%Y-%m-%dT%H:%M:%S", ct)
        return f"{base}.{int(record.msecs):03d}Z"
    ct = time.localtime(record.created)
    # seconds-only precision (no millis); two spaces between the date
    # and the time in the file format (the terminal shows time only).
    return time.strftime("%Y-%m-%d  %H:%M:%S" if include_date else "%H:%M:%S", ct)


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
    "CRASH": "38;5;167",
    "ENV": "38;5;103",
    "LAUNCHER": "38;5;110",
    "MIC-WATCHER": "38;5;215",
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
    "A11Y": "38;5;102",
    "AI_ENHANCE": "38;5;140",
    "APP": "38;5;102",
    "AUDIO-PROC": "38;5;215",
    "AUTOSTART": "38;5;103",
    "BUNDLE-ID": "38;5;102",
    "CLIPBOARD-AUDIT": "38;5;120",
    "CLIPBOARD-SNAPSHOT": "38;5;120",
    "CONFIG-SANITIZER": "38;5;102",
    "CRASH-BUF": "38;5;167",
    "CREDENTIAL_STORE": "38;5;102",
    "CUDA-DLL": "38;5;75",
    "DISK": "38;5;102",
    "GPU": "38;5;75",
    "HIGHPASS": "38;5;69",
    "HOTKEY-WAYLAND": "38;5;141",
    "INIT": "38;5;103",
    "LEVEL-MON": "38;5;215",
    "LOG-SETUP": "38;5;102",
    "MIC-WATCHER-CA": "38;5;215",
    "NATIVE-BINARY": "38;5;102",
    "NATIVE-HOTKEY": "38;5;141",
    "NOISE-SUPPRESS": "38;5;69",
    "NOTCH": "38;5;69",
    "PACK": "38;5;102",
    "PAUSE": "38;5;215",
    "PERF": "38;5;111",
    "PERMISSION": "38;5;111",
    "PLATFORM": "38;5;102",
    "PREWARM": "38;5;103",
    "PROC-CHAIN": "38;5;102",
    "QWEN-ONNX": "38;5;69",
    "RECORDER": "38;5;79",
    "RESOURCE": "38;5;102",
    "SECURITY": "38;5;167",
    "SERVICE": "38;5;110",
    "SESSION": "38;5;102",
    "SIDECAR-ENV": "38;5;110",
    "SIDECAR-WS": "38;5;110",
    "START": "38;5;103",
    "TASK": "38;5;102",
    "TEMPLATES": "38;5;102",
    "THREAD-REGISTRY": "38;5;102",
    "TIMER": "38;5;102",
    "UNDO": "38;5;120",
    "UNINSTALL": "38;5;95",
    "UPDATE": "38;5;110",
    "VOCAB": "38;5;102",
    "VOCAB_AUTO": "38;5;102",
    "VOLUME-CRASH": "38;5;167",
    "VOLUME-LINUX": "38;5;111",
    "VOLUME-MAC": "38;5;111",
    "VOLUME-WIN": "38;5;111",
    "WS": "38;5;110",
    "XRUN": "38;5;215",
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

    previously this was an O(N*K) linear scan that called
    ``msg.lower()`` once and then ran ~80 ``kw in lower`` substring
    checks per INFO record. The precompiled alternation regex below
    performs the same first-match-wins lookup in a single pass over the
    string. The regex is built once at import time from
    :data:`_TOPIC_KEYWORDS` so it stays in sync with the keyword table.
    """
    if not msg:
        return None
    m = _TOPIC_KEYWORDS_REGEX.search(msg)
    if m is None:
        return None
    return m.lastgroup


def _build_topic_keywords_regex():
    """compile a single named-group alternation regex from
    :data:`_TOPIC_KEYWORDS`. First-match-wins is preserved by emitting
    each topic's keywords in their declared order, and topics in their
    declared order -- Python's ``re`` alternation is leftmost-first.
    """
    import re

    parts: list[str] = []
    for topic, keywords in _TOPIC_KEYWORDS.items():
        if not keywords:
            continue
        # Sort each topic's keywords by length descending so the longer
        # (more specific) phrases win over their prefixes within the
        # same topic (e.g. "transcription thread" before "transcrib").
        ordered = sorted(keywords, key=len, reverse=True)
        group = "|".join(re.escape(kw) for kw in ordered if kw)
        if not group:
            continue
        parts.append(f"(?P<{topic}>{group})")
    if not parts:
        # No keywords -- return a regex that never matches.
        return re.compile(r"(?!x)x")
    return re.compile("|".join(parts), re.IGNORECASE)


_TOPIC_KEYWORDS_REGEX = _build_topic_keywords_regex()


def _extract_topic(msg: str) -> tuple[str | None, str]:
    """Extract a bracketed topic prefix (e.g. ``[PARAKEET]``) from *msg*.

    Returns ``(topic, rest)``.
    """
    if msg.startswith("[") and "]" in msg:
        close = msg.index("]")
        return msg[1:close], msg[close + 1 :].lstrip()
    return None, msg


# ── Colour formatters ─────────────────────────────────────────────────


def _append_exception_text(
    formatter: logging.Formatter,
    record: logging.LogRecord,
    line: str,
) -> str:
    """append ``exc_text`` / ``stack_info`` to a formatted log line.

    Python's stock ``logging.Formatter.format`` does this; custom
    overrides that skip ``super().format()`` must replicate it or
    ``log.exception(...)`` / ``log.error(..., exc_info=True)`` lose
    their tracebacks — the single most important diagnostic field for
    remote triage. ``PIIRedactionFilter`` (when attached to the handler)
    has already cached a *redacted* traceback in ``record.exc_text``;
    we honour it to avoid re-running the (potentially expensive)
    traceback formatting and to preserve the PII scrub.

    Shared by :class:`_ColorFormatter`, :class:`_FileFormatter`, and
    :class:`_JsonFormatter` (DRY — Rule 24).
    """
    if record.exc_info and not record.exc_text:
        record.exc_text = formatter.formatException(record.exc_info)
    if record.exc_text:
        if not line.endswith("\n"):
            line += "\n"
        line += record.exc_text
    if record.stack_info:
        if not line.endswith("\n"):
            line += "\n"
        line += formatter.formatStack(record.stack_info)
    return line


class _ColorFormatter(logging.Formatter):
    """ANSI-coloured formatter for stderr (terminal output).

    Design
    ------
    - Clean time-only timestamp (``HH:MM:SS``, no date) dimmed to
      recede visually — the date lives only in the log file
    - INFO level label omitted (redundant on ~every line)
    - WARN / ERR / FATAL full-line coloured with level label
    - Lines with ``[TOPIC]`` prefix coloured by topic
    - Lines without prefix infer topic from content keywords
    """

    _DIM = "38;5;242"  # grey
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
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        # terminal shows time only (no date, no millis).
        ts = _iso_timestamp(record, include_date=False)
        msg = record.getMessage()
        topic, _ = _extract_topic(msg)

        if record.levelno >= logging.WARNING:
            c = self._LVL_COLOR.get(record.levelno, "0")
            sym = self._LVL_SYM.get(record.levelno, "????")
            # Full-line colour: emit colour, ts, then reset.
            line = f"\033[{c}m{ts}  {sym:<5} {msg}\033[0m"
        else:
            # INFO — dim timestamp, no level label,
            # message coloured by topic.
            prefix = f"\033[{self._DIM}m{ts}\033[0m"
            tc = _TOPIC_COLOR.get(topic) if topic else None
            if tc is None and not topic:
                inferred = _infer_topic(msg)
                if inferred:
                    tc = _TOPIC_COLOR.get(inferred)
            body = f"\033[{tc}m{msg}\033[0m" if tc else msg
            line = f"{prefix}  {body}"

        # append exception traceback. ``PIIRedactionFilter``
        # pre-formats and redacts the traceback into ``record.exc_text``
        # before any formatter runs, so we honour it here. Plain text
        # (no ANSI) so the traceback is readable on every terminal.
        line = _append_exception_text(self, record, line)
        return line


class _FileFormatter(logging.Formatter):
    """Clean plain-text formatter for the ``voice-typer.log`` file.

    The file always contains clean, plain text without any ANSI escape
    codes.  If you need coloured log output, use the terminal stderr
    stream (which uses ``_ColorFormatter``).

    Format::

        2026-07-15  12:34:56  INFO  [HOTKEY] RegisterHotKey OK
        2026-07-15  12:34:56  WARN  [ENV] Invalid value ...
        2026-07-15  12:34:56  ERROR Stream end

    Fields (left to right):

    - ``ts``       — clean space-separated local timestamp
      (``YYYY-MM-DD  HH:MM:SS`` — two spaces between the date and the
      time) with seconds precision.  No ``T`` separator, no
      timezone offset, no millisecond fraction — the line reads
      naturally.
    - ``label``    — short level label (``DEBUG`` / ``INFO`` / ``WARN`` /
      ``ERROR`` / ``CRITICAL``), left-padded to a fixed 5-char column so
      the message starts at the same position for every level: 4-char
      labels (``INFO`` / ``WARN``) get TWO spaces after, 5-char labels
      (``DEBUG`` / ``ERROR``) get ONE space after.  Mirrors the Rust
      host's ``{:5}`` level padding (``combined.rs``) so the Python and
      Rust log streams align line-for-line.
    - ``msg``      — the redacted log message (its ``[TOPIC]`` prefix
      already identifies the subsystem, so no separate component
      column is needed).

    The session id, thread name, and module path are deliberately NOT
    rendered: they add noise to every line without helping the user
    read the log.  Correlation metadata is still available in JSON
    mode (``VOICE_TYPER_LOG_JSON=1``) for operators who need it.
    """

    _LVL_LABEL = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARN",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts = _iso_timestamp(record)
        msg = record.getMessage()
        label = self._LVL_LABEL.get(record.levelno, "INFO")
        line = f"{ts}  {label:<5} {msg}"
        # append the (already PII-redacted) traceback so
        # ``log.exception(...)`` / ``log.error(..., exc_info=True)``
        # records keep their diagnostic stack trace in the file.
        line = _append_exception_text(self, record, line)
        return line


class _JsonFormatter(logging.Formatter):
    """Structured JSON formatter  — opt-in via ``VOICE_TYPER_LOG_JSON=1``.

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
      ``KeyError``-prone ``.get()`` fallbacks.
    - ``thread`` is the emitting thread name (always present).
      ``task`` is the Python 3.12+ asyncio task name, omitted entirely
      when not in scope (synchronous call sites) so the common case
      stays compact.
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
            # ISO 8601 UTC with millis + ``Z`` suffix so log
            # aggregators get a parseable, timezone-aware timestamp
            # (no manual tz inference needed for cross-timezone tickets).
            "ts": _iso_timestamp(record, utc=True),
            "level": record.levelname,
            "component": getattr(record, "component", record.name),
            # emit ``session_id`` so JSON aggregators can group
            # lines by process session (empty string when ``_SessionFilter``
            # has not run — present-but-empty keeps the schema flat).
            "session_id": getattr(record, "session_id", ""),
            # emit the emitting thread name so threaded pipelines
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
        # ``payload["message"]`` is already a ``str`` (from
        # ``record.getMessage()``), so no coercion is needed.
        topic, _ = _extract_topic(payload["message"])
        if topic:
            payload["topic"] = topic
        # Correlation id from the execution context (IPC request id /
        # dictation cycle id).  Omitted when not in scope.
        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        # include the (PII-redacted) traceback so JSON aggregators
        # can index / alert on stack traces. ``PIIRedactionFilter`` has
        # already cached the redacted text in ``record.exc_text``.
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            payload["traceback"] = record.exc_text.rstrip()
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info).rstrip()
        return json.dumps(payload, ensure_ascii=False)


__all__ = [
    "_ColorFormatter",
    "_FileFormatter",
    "_JsonFormatter",
    "_TOPIC_COLOR",
    "_TOPIC_KEYWORDS",
    "_TOPIC_KEYWORDS_REGEX",
    "_append_exception_text",
    "_build_topic_keywords_regex",
    "_extract_topic",
    "_infer_topic",
    "_iso_timestamp",
]
