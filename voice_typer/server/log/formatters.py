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


def _iso_timestamp(record: logging.LogRecord, *, utc: bool = False) -> str:
    """Return an ISO 8601 timestamp with milliseconds and timezone.

    ``logging.Formatter.formatTime`` with a custom format string
    bypasses Python's ``%(msecs)`` / ``%(asctime)`` defaults and drops
    both milliseconds and the timezone offset.  For an audio app that
    pushes ``bubble_level`` events at ~60 Hz, two log lines within the
    same second are indistinguishable, and cross-timezone support
    tickets require manual timezone inference.

    By default the local-time zone offset is appended (``+0200``);
    pass ``utc=True`` for the JSON formatter which emits a Z-suffixed
    UTC timestamp (``...Z``) that log aggregators expect.
    """
    if utc:
        ct = time.gmtime(record.created)
        base = time.strftime("%Y-%m-%dT%H:%M:%S", ct)
        return f"{base}.{int(record.msecs):03d}Z"
    ct = time.localtime(record.created)
    base = time.strftime("%Y-%m-%dT%H:%M:%S", ct)
    tz = time.strftime("%z", ct) or "+0000"
    return f"{base}.{int(record.msecs):03d}{tz}"


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
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        # ISO 8601 with millis + tz so sub-second audio events
        # are distinguishable. ISO 8601 requires a 2-digit hour, so we
        # do NOT trim the leading zero (the legacy ``%H:%M:%S`` trim
        # was a cosmetic preference that breaks ISO parsing).
        ts = _iso_timestamp(record)
        msg = record.getMessage()
        topic, _ = _extract_topic(msg)
        # the per-process ``[hex session_id]`` bracket is rendered
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
            line = f"\033[{c}m{ts}  \033[{self._DIM_ATTR}m[{session_id}]\033[22m  {sym} {msg}\033[0m"
        else:
            # INFO — dim timestamp, dim session_id bracket, no level label,
            # message coloured by topic.
            prefix = f"\033[{self._DIM}m{ts}\033[0m"
            tc = _TOPIC_COLOR.get(topic) if topic else None
            if tc is None and not topic:
                inferred = _infer_topic(msg)
                if inferred:
                    tc = _TOPIC_COLOR.get(inferred)
            body = f"\033[{tc}m{msg}\033[0m" if tc else msg
            line = f"{prefix}  {bracket}  {body}"

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

    Format (docstring updated to match the real ISO 8601 format
    with millis + tz offset)::

        2026-07-15T12:34:56.789+0200  [a3f1b2c4]  [MainThread]  INFO   [voice_typer.server.app]  RegisterHotKey OK
        2026-07-15T12:34:56.789+0200  [a3f1b2c4]  [MainThread]  WARN   [voice_typer.server.app]  [ENV] Invalid value ...
        2026-07-15T12:34:56.789+0200  [a3f1b2c4]  [Transcribe]  ERR  [voice_typer.server.dictation_pipeline]  Stream end

    Fields (left to right):

    - ``ts``                 — ISO 8601 timestamp
      (``YYYY-MM-DDTHH:MM:SS.mmm±HHMM``) with ``T`` date/time
      separator, millisecond precision, and a signed tz offset.  The
      millis are required so sub-second audio events (VAD triggers,
      chunk boundaries) are distinguishable in the file log .
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
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        # ISO 8601 with millis + tz so sub-second audio events
        # are distinguishable in the file log.
        ts = _iso_timestamp(record)
        msg = record.getMessage()
        label = self._LVL_LABEL.get(record.levelno, "INFO ")
        # render the per-process session_id bracket so operators
        # can correlate log lines across process restarts.  ``--------``
        # placeholder when ``_SessionFilter`` has not injected the attribute
        # (third-party loggers, early startup, unit-test records built by
        # hand) — empty brackets look like a bug.
        session_id = getattr(record, "session_id", "") or "--------"
        # render the component (module/logger name) so operators
        # can tell which subsystem produced a line without parsing the
        # message text.  Defaults to ``record.name`` for records that
        # bypass ``_SessionFilter``.
        component = getattr(record, "component", record.name)
        # render the emitting thread name so threaded pipelines
        # (transcription thread, prewarm pipeline, IPC workers) can be
        # distinguished in the log.  ``taskName`` (Python 3.12+) is
        # emitted inline when an asyncio task is in scope.
        thread_name = getattr(record, "threadName", "") or ""
        task_bracket = ""
        task_name = getattr(record, "taskName", None)
        if task_name:
            task_bracket = f"  [{task_name}]"
        line = f"{ts}  [{session_id}]  [{thread_name}]{task_bracket}  {label}  [{component}]  {msg}"
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
