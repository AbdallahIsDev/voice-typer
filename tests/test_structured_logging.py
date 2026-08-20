"""structured (JSON) logging + correlation-id propagation.

Covers:
* ``_JsonFormatter`` emits a flat, stable JSON schema with
  ts / level / component / session_id / message.
* ``topic`` and ``correlation_id`` appear only when present (no noise
  in the common case).
* PIIRedactionFilter still scrubs secrets before the JSON formatter
  runs (same guarantee as the text formatters).
* ``VOICE_TYPER_LOG_JSON`` env gate selects the JSON formatter in
  ``setup_logging`` (end-to-end: log line on disk is valid JSON).
* Correlation id flows through a ``_correlation_id`` context manager
  and resets cleanly afterwards.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from voice_typer.server.log import (
    _correlation_id,
    _json_logging_enabled,
    _JsonFormatter,
    get_correlation_id,
    reset,
    reset_correlation_id,
    set_correlation_id,
    setup_logging,
)


def _record(msg, *, level=logging.INFO, name="voice_typer.server.fake", session="a3f1b2c4"):
    rec = logging.LogRecord(name, level, "x.py", 1, msg, (), None)
    rec.session_id = session
    rec.component = name
    return rec


# ─── Schema ────────────────────────────────────────────────────────────


def test_json_formatter_flat_schema() -> None:
    """A basic line carries the required flat keys, in a valid JSON object."""
    line = _JsonFormatter().format(_record("[HOTKEY] RegisterHotKey succeeded"))
    parsed = json.loads(line)
    assert parsed["level"] == "INFO"
    assert parsed["component"] == "voice_typer.server.fake"
    assert parsed["session_id"] == "a3f1b2c4"
    assert parsed["message"] == "[HOTKEY] RegisterHotKey succeeded"
    assert "ts" in parsed


def test_json_formatter_topic_only_when_present() -> None:
    """``topic`` is emitted for ``[TOPIC]``-prefixed messages, omitted otherwise."""
    with_topic = json.loads(_JsonFormatter().format(_record("[RECORDING] mic opened")))
    assert with_topic["topic"] == "RECORDING"

    without = json.loads(_JsonFormatter().format(_record("plain status line")))
    assert "topic" not in without


def test_json_formatter_correlation_only_when_present() -> None:
    """``correlation_id`` appears only when one is in context (keeps lines compact)."""
    tok = set_correlation_id("#7")
    try:
        with_corr = json.loads(_JsonFormatter().format(_record("[DICTATION] start")))
        assert with_corr["correlation_id"] == "#7"
    finally:
        reset_correlation_id(tok)

    without = json.loads(_JsonFormatter().format(_record("[DICTATION] start")))
    assert "correlation_id" not in without


def test_json_formatter_level_name_not_label() -> None:
    """JSON uses the canonical level *name* (INFO/WARNING), not the text label (WARN)."""
    warn = json.loads(_JsonFormatter().format(_record("[ENV] bad", level=logging.WARNING)))
    assert warn["level"] == "WARNING"
    assert warn["level"] != "WARN "


def test_json_formatter_unicode_preserved() -> None:
    """``ensure_ascii=False`` keeps non-ASCII (transcriptions, device names) readable."""
    line = _JsonFormatter().format(_record("café — 日本語 — naïve"))
    parsed = json.loads(line)
    assert "café" in parsed["message"]
    assert "日本語" in parsed["message"]


# ─── PII redaction still applies ────────────────────────────────────────


def test_json_formatter_pii_redacted() -> None:
    """the PII filter runs before the formatter, so JSON is scrubbed too."""
    from voice_typer.server.security import PIIRedactionFilter

    rec = _record("User test@example.com logged in")
    PIIRedactionFilter().filter(rec)  # mutates record.msg (as in setup_logging)
    out = _JsonFormatter().format(rec)
    assert "test@example.com" not in out
    assert "[EMAIL]" in out
    # Must still be valid JSON after redaction.
    json.loads(out)


def test_json_formatter_api_key_redacted() -> None:
    """API-key redaction survives the JSON path."""
    from voice_typer.server.security import PIIRedactionFilter

    rec = _record("Using sk-abcdefghijklmnopqrstuvwxyz123456 in call")
    PIIRedactionFilter().filter(rec)
    out = _JsonFormatter().format(rec)
    assert "sk-abcdef" not in out
    assert "***" in out
    json.loads(out)


# ─── Env gate ─────────────────────────────────────────────────────────


def test_json_env_gate_default_off(monkeypatch) -> None:
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    assert _json_logging_enabled() is False


def test_json_env_gate_on(monkeypatch) -> None:
    monkeypatch.setenv("VOICE_TYPER_LOG_JSON", "1")
    assert _json_logging_enabled() is True
    monkeypatch.setenv("VOICE_TYPER_LOG_JSON", "true")
    assert _json_logging_enabled() is True


# ─── End-to-end: setup_logging picks JSON formatter when gated ────────


def test_setup_logging_emits_json_when_gated(tmp_path: Path, monkeypatch) -> None:
    """With VOICE_TYPER_LOG_JSON=1 the file log contains valid JSON lines."""
    monkeypatch.setenv("VOICE_TYPER_LOG_JSON", "1")
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    setup_logging(config_dir)

    try:
        log = logging.getLogger("voice_typer.server.fake_module")
        tok = set_correlation_id("req-42")
        try:
            log.info("[HOTKEY] RegisterHotKey succeeded")
        finally:
            reset_correlation_id(tok)

        for h in logging.getLogger("voice_typer").handlers:
            with __import__("contextlib").suppress(Exception):
                h.flush()

        content = (config_dir / "logs" / "voice-typer.log").read_text(encoding="utf-8")
        # JSON mode: every non-empty line must parse as JSON.
        parsed = [json.loads(ln) for ln in content.splitlines() if ln.strip()]
        assert parsed, "no log lines written"
        hotkey_line = next(p for p in parsed if "RegisterHotKey" in p["message"])
        assert hotkey_line["correlation_id"] == "req-42"
        assert hotkey_line["topic"] == "HOTKEY"
    finally:
        reset()


def test_setup_logging_default_is_text(tmp_path: Path, monkeypatch) -> None:
    """Default (gate off) keeps the human-readable text format — regression guard."""
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    setup_logging(config_dir)

    try:
        log = logging.getLogger("voice_typer.server.fake_module")
        log.info("[HOTKEY] RegisterHotKey succeeded")
        for h in logging.getLogger("voice_typer").handlers:
            with __import__("contextlib").suppress(Exception):
                h.flush()

        content = (config_dir / "logs" / "voice-typer.log").read_text(encoding="utf-8")
        # Text format: a session_id bracket + level label, not JSON braces.
        # Text format: a session_id bracket + level label, not JSON braces.
        # The session id is generated at runtime (uuid4 hex), so the literal
        # ``a3f1b2c4`` placeholder will NOT be in the file — the actual
        # 8-char session_id (matching ``[0-9a-f]{8}``) will be.
        assert "[a3f1b2c4]" not in content
        assert "INFO" in content
        assert "[HOTKEY] RegisterHotKey succeeded" in content
        # Not JSON: the line does not start with '{'
        assert not content.lstrip().startswith("{")
    finally:
        reset()


# ─── Correlation-id context manager ───────────────────────────────────


def test_correlation_id_context_manager_sets_and_resets() -> None:
    """``_correlation_id`` publishes the id in its block and clears it after."""
    assert get_correlation_id() == ""
    with _correlation_id("#9"):
        assert get_correlation_id() == "#9"
    assert get_correlation_id() == ""


def test_correlation_id_context_manager_noop_on_empty() -> None:
    """An empty/None id is a no-op (doesn't clobber an outer id)."""
    tok = set_correlation_id("outer")
    try:
        with _correlation_id(""):
            assert get_correlation_id() == "outer"
    finally:
        reset_correlation_id(tok)


def test_set_reset_token_roundtrip_restores_prior() -> None:
    """``set_correlation_id`` returns a token whose ``reset`` restores the
    *previous* value — the primitive that stops concurrent requests from
    leaking each other's correlation id."""
    assert get_correlation_id() == ""
    tok_a = set_correlation_id("req-A")
    try:
        assert get_correlation_id() == "req-A"
        # Nested set overrides within this context...
        tok_b = set_correlation_id("req-B")
        assert get_correlation_id() == "req-B"
        # ...and resetting the inner token returns to the outer value,
        # NOT to the empty default.
        reset_correlation_id(tok_b)
        assert get_correlation_id() == "req-A"
    finally:
        reset_correlation_id(tok_a)
    # Fully unwound back to empty.
    assert get_correlation_id() == ""
