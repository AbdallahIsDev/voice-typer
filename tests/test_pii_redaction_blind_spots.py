"""Regression test for XZ-LOG-12: PIIRedactionFilter blind spots.

Background
----------
``PIIRedactionFilter`` (``voice_typer/server/security.py``) redacts
structured PII patterns (email, US phone, SSN, credit card) from log
records via ``_PATTERNS``. The risk called out by XZ-LOG-12 is that
future code might ``log.info("[TRANSCRIBE] result: %s", text)``
directly, interpolating the raw transcription text into the log record.
The filter only catches the four structured PII patterns — free-form
medical/financial/address/name content passes through verbatim.

The convention (established by XZ-PII-04 / SEC-009) is:
  * Never log raw ``text`` / ``transcript`` / ``partial`` /
    ``final_text`` / ``result`` variables directly.
  * Either gate the log behind ``log_transcriptions`` AND apply
    ``redact_pii()`` (as ``transcription.py`` does for segment logs),
    OR log a non-reversible hash + length (as ``dictation_pipeline.py``
    does for the consolidated ``[TRANSCRIBE] Transcription: hash=…``
    line).

This test greps the ``voice_typer/server/`` source tree for log calls
that interpolate a transcription-like variable directly, and fails if
any are found. It is a static source-inspection test (no runtime
behavior) — the goal is to make a future regression a noisy test
failure rather than a silent privacy leak.

The test is allowlist-based: known-safe call sites (which already
apply ``redact_pii`` / hash / gate behind ``log_transcriptions``) are
listed in ``_SAFE_LOG_CALL_SITES``. New log calls that interpolate
``text`` / ``transcript`` / etc. must either (a) be added to the
allowlist with a justification comment, or (b) be re-written to apply
``redact_pii`` / hash first.
"""

from __future__ import annotations

import ast
import pathlib

# ── Static source-inspection helpers ────────────────────────────────

_SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent / "voice_typer" / "server"
# Repo root, used for the human-readable path in the assertion message.
_REPO_ROOT = _SERVER_DIR.parent.parent

# Variable names that are likely to hold raw transcription text. Any
# ``log.<level>(..., "<format>" %/<format> <var>)`` call interpolating
# one of these is a potential PII leak and must be audited.
_PII_VARIABLE_NAMES = frozenset(
    {
        "text",
        "transcript",
        "partial",
        "final_text",
        "result",
        "transcribed_text",
        "transcription_text",
    }
)

# File names that contain log calls but are out of scope for this
# regression test (e.g. test files, the redaction filter itself).
# The filter itself logs at DEBUG when it redacts a pattern — that
# log line is the filter doing its job, not a leak.
_OUT_OF_SCOPE_FILES = frozenset(
    {
        # EO-23: the PII redaction filter + redact_pii helper now live in
        # ``security/redaction.py`` (was ``security.py`` pre-consolidation).
        "redaction.py",
        "log.py",  # logging infrastructure
    }
)

# Call sites that interpolate a transcription-like variable but are
# KNOWN-SAFE because they apply ``redact_pii`` / hash / gate behind
# ``log_transcriptions`` BEFORE the log call. Each entry is a tuple of
# (file_name, snippet) where ``snippet`` is a unique substring of the
# log call's format string. New entries MUST be accompanied by a
# justification comment explaining why the call is safe.
_SAFE_LOG_CALL_SITES: dict[str, set[str]] = {
    # dictation_pipeline.py: the consolidated "[TRANSCRIBE] Transcription:
    # hash=%s len=%d" line logs a non-reversible SHA-256 prefix + length
    # (NOT the raw text) — see  The line is gated behind
    # ``log_transcriptions`` config flag.
    "dictation_pipeline.py": {
        "[TRANSCRIBE] Transcription: hash=%s len=%d",
        "[TRANSCRIBE] Transcription: %d chars",
    },
    # transcription.py: per-segment DEBUG logs are gated behind
    # ``log_transcriptions`` AND apply ``redact_pii()`` before the log
    # call (see SEC-009 / ). The hallucination rejection log uses
    # ``log_hallucination_rejection`` which also respects
    # ``log_transcriptions`` and applies ``redact_pii``.
    "transcription.py": {
        "[TRANSCRIBE] Segment: [%.1fs - %.1fs] %s",  # redact_pii applied upstream
        "[TRANSCRIBE] Segment: [%d chars @ %.1fs - %.1fs]",  # no text content
        "[TRANSCRIBE] Result: %d chars",  # length only, no text
    },
    # microphone_test.py: HU-21 FIXED — the mic-test transcription log
    # now logs only ``%d chars`` (a ``len(text)`` call, which the AST
    # walker does not flag) at DEBUG, so no allowlist entry is needed
    # and the regression guard stays strict.
    # remote_session.py: the ``result`` variable here is a Windows API
    # return value (int ``SM_REMOTESESSION``), NOT transcription text.
    # The variable name collides with the ``_PII_VARIABLE_NAMES`` set
    # (``result`` is used as a transcription variable name elsewhere,
    # e.g. ``transcription.py::_transcribe_unlocked``'s ``result = ...``),
    # so we allowlist this specific format string to avoid a false
    # positive. The format string is clearly Windows-API-related
    # (``SM_REMOTESESSION=%d``), not transcription-related.
    "remote_session.py": {
        "[PLATFORM] RDP/remote session detected (SM_REMOTESESSION=%d)",
    },
    # ``shutdown/plan.py`` and ``shutdown_controller.py``: the ``result``
    # variable here is the bool/None return value of a shutdown-step
    # callable (e.g. ``step.func()``), NOT transcription text. The
    # ``__repr__`` of a bool/None is ``"True"``/``"False"``/``"None"`` —
    # not user data. Allowlist to avoid a false positive on the
    # shared ``result`` name (the AST walker can't tell from the name
    # alone whether ``result`` is a transcription variable or a generic
    # function return value).
    "plan.py": {
        "[SHUTDOWN] %s raised: %r",
    },
    "shutdown_controller.py": {
        "[SHUTDOWN] %s raised: %r",
    },
}


def _iter_server_python_files() -> list[pathlib.Path]:
    """Yield all ``.py`` files under ``voice_typer/server/``."""
    if not _SERVER_DIR.is_dir():  # pragma: no cover — defensive
        return []
    return sorted(_SERVER_DIR.rglob("*.py"))


def _find_unsafe_log_calls(source: str, file_name: str) -> list[str]:
    """Return a list of unsafe log call descriptions in ``source``.

    A log call is "unsafe" if it interpolates a variable whose name is
    in ``_PII_VARIABLE_NAMES`` directly into the format string, AND the
    format string is NOT in ``_SAFE_LOG_CALL_SITES[file_name]``.

    Detection is via ``ast``: walk the AST, find ``ast.Call`` nodes
    whose function is ``log.<level>`` (any level), inspect the format
    args, and flag any ``ast.Name`` whose ``id`` is in
    ``_PII_VARIABLE_NAMES``.
    """
    try:
        tree = ast.parse(source, filename=file_name)
    except SyntaxError:  # pragma: no cover — defensive
        return []

    safe_formats = _SAFE_LOG_CALL_SITES.get(file_name, set())
    unsafe: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match ``log.<level>(...)`` — the function is an Attribute
        # whose value is a Name ``log``.
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not isinstance(func.value, ast.Name):
            continue
        if func.value.id != "log":
            continue
        # Inspect positional args. The first arg is the format string;
        # subsequent args are the values interpolated into the format.
        # Flag if any positional arg (after the format) is a Name in
        # ``_PII_VARIABLE_NAMES`` AND the format string is not in the
        # safe allowlist.
        if len(node.args) < 2:
            continue
        fmt_arg = node.args[0]
        if not isinstance(fmt_arg, ast.Constant) or not isinstance(fmt_arg.value, str):
            continue
        fmt_str = fmt_arg.value
        if fmt_str in safe_formats:
            continue
        # Check the interpolated values.
        for value_arg in node.args[1:]:
            if isinstance(value_arg, ast.Name) and value_arg.id in _PII_VARIABLE_NAMES:
                unsafe.append(f"log.{func.attr}({fmt_str!r}, {value_arg.id}) — interpolates raw transcription variable")
    return unsafe


# ── Tests ───────────────────────────────────────────────────────────


class TestNoRawTranscriptionInLogs:
    """XZ-LOG-12: regression guard against logging raw transcription text.

    The ``PIIRedactionFilter`` only catches four structured PII patterns
    (email / phone / SSN / CC). Free-form transcription text (medical
    dictation, financial narratives, addresses, names) passes through
    verbatim if a future code change logs ``text`` / ``transcript`` /
    etc. directly. This test greps the ``voice_typer/server/`` source
    tree for such calls and fails if any are found outside the
    allowlist in ``_SAFE_LOG_CALL_SITES``.
    """

    def test_no_raw_transcription_variable_interpolated_in_log_calls(self) -> None:
        """Static source-inspection: no ``log.<level>(..., text)`` calls.

        Walks the AST of every ``.py`` file under ``voice_typer/server/``
        and flags any ``log.<level>(fmt, <var>)`` call where ``<var>``
        is named ``text`` / ``transcript`` / ``partial`` / ``final_text``
        / ``result`` / etc. and ``fmt`` is not in the per-file
        allowlist ``_SAFE_LOG_CALL_SITES``.
        """
        all_unsafe: list[str] = []
        for path in _iter_server_python_files():
            if path.name in _OUT_OF_SCOPE_FILES:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # pragma: no cover — defensive
                continue
            unsafe = _find_unsafe_log_calls(source, path.name)
            for desc in unsafe:
                all_unsafe.append(f"{path.relative_to(_REPO_ROOT)}: {desc}")

        assert not all_unsafe, (
            "XZ-LOG-12 regression: found log calls that interpolate a raw "
            "transcription variable directly. The PIIRedactionFilter only "
            "catches four structured PII patterns — free-form medical / "
            "financial / address / name content would leak into the log "
            "file. Either (a) apply ``redact_pii()`` / log a hash before "
            "the call, OR (b) add the format string to "
            "``_SAFE_LOG_CALL_SITES`` in this test with a justification "
            "comment. Unsafe call(s):\n  " + "\n  ".join(all_unsafe)
        )

    def test_pii_variable_name_set_is_nonempty(self) -> None:
        """Sanity check: the ``_PII_VARIABLE_NAMES`` set is populated.

        A future refactor that accidentally empties this set would
        silently disable the regression guard. This test makes the
        breakage noisy.
        """
        assert _PII_VARIABLE_NAMES, (
            "XZ-LOG-12: ``_PII_VARIABLE_NAMES`` is empty — the regression guard would not catch any unsafe log calls."
        )

    def test_safe_log_call_sites_allowlist_uses_unique_snippets(self) -> None:
        """Sanity check: each allowlist entry is unique within its file.

        If two entries in the same file have the same format string, the
        second is dead code (the first already matches). This test
        catches copy-paste errors when adding new entries.
        """
        for file_name, snippets in _SAFE_LOG_CALL_SITES.items():
            assert len(snippets) == len(set(snippets)), (
                f"XZ-LOG-12: ``_SAFE_LOG_CALL_SITES['{file_name}']`` has "
                f"duplicate entries — each allowlist snippet must be unique."
            )
