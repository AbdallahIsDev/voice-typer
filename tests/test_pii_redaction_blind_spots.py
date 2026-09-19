"""
Regression test for XZ-LOG-12: PIIRedactionFilter blind spots.
The convention (established by XZ-PII-04 / SEC-009) is:
"""

from __future__ import annotations

import ast
import pathlib

_SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent / "voice_typer" / "server"
# Repo root, used for the human-readable path in the assertion message.
_REPO_ROOT = _SERVER_DIR.parent.parent

# Variable names that are likely to hold raw transcription text. Any
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
_OUT_OF_SCOPE_FILES = frozenset(
    {
        "redaction.py",
        "log.py",  # logging infrastructure
    }
)

# Call sites that interpolate a transcription-like variable but are
_SAFE_LOG_CALL_SITES: dict[str, set[str]] = {
    "dictation_pipeline.py": {
        "[TRANSCRIBE] Transcription: hash=%s len=%d",
        "[TRANSCRIBE] Transcription: %d chars",
    },
    # call (see SEC-009 / ). The hallucination rejection log uses
    "transcription.py": {
        "[TRANSCRIBE] Segment: [%.1fs - %.1fs] %s",  # redact_pii applied upstream
        "[TRANSCRIBE] Segment: [%d chars @ %.1fs - %.1fs]",  # no text content
        "[TRANSCRIBE] Result: %d chars",  # length only, no text
    },
    "remote_session.py": {
        "[PLATFORM] RDP/remote session detected (SM_REMOTESESSION=%d)",
    },
    # ``shutdown/plan.py`` and ``shutdown_controller.py``: the ``result``
    "plan.py": {
        "[SHUTDOWN] %s raised: %r",
    },
    "shutdown_controller.py": {
        "[SHUTDOWN] %s raised: %r",
    },
}


def _iter_server_python_files() -> list[pathlib.Path]:
    """Yield all ``.py`` files under ``voice_typer/server/``."""
    if not _SERVER_DIR.is_dir():  # pragma: no cover, defensive
        return []
    return sorted(_SERVER_DIR.rglob("*.py"))


def _find_unsafe_log_calls(source: str, file_name: str) -> list[str]:
    """Return a list of unsafe log call descriptions in ``source``."""
    try:
        tree = ast.parse(source, filename=file_name)
    except SyntaxError:  # pragma: no cover, defensive
        return []

    safe_formats = _SAFE_LOG_CALL_SITES.get(file_name, set())
    unsafe: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match ``log.<level>(...)``, the function is an Attribute
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not isinstance(func.value, ast.Name):
            continue
        if func.value.id != "log":
            continue
        # Inspect positional args. The first arg is the format string;
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
                unsafe.append(f"log.{func.attr}({fmt_str!r}, {value_arg.id}), interpolates raw transcription variable")
    return unsafe


class TestNoRawTranscriptionInLogs:
    """XZ-LOG-12: regression guard against logging raw transcription text."""

    def test_no_raw_transcription_variable_interpolated_in_log_calls(self) -> None:
        """Static source-inspection: no ``log.<level>(..., text)`` calls."""
        all_unsafe: list[str] = []
        for path in _iter_server_python_files():
            if path.name in _OUT_OF_SCOPE_FILES:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # pragma: no cover, defensive
                continue
            unsafe = _find_unsafe_log_calls(source, path.name)
            for desc in unsafe:
                all_unsafe.append(f"{path.relative_to(_REPO_ROOT)}: {desc}")

        assert not all_unsafe, (
            "XZ-LOG-12 regression: found log calls that interpolate a raw "
            "transcription variable directly. The PIIRedactionFilter only "
            "catches four structured PII patterns, free-form medical / "
            "financial / address / name content would leak into the log "
            "file. Either (a) apply ``redact_pii()`` / log a hash before "
            "the call, OR (b) add the format string to "
            "``_SAFE_LOG_CALL_SITES`` in this test with a justification "
            "comment. Unsafe call(s):\n  " + "\n  ".join(all_unsafe)
        )

    def test_pii_variable_name_set_is_nonempty(self) -> None:
        """Sanity check: the ``_PII_VARIABLE_NAMES`` set is populated."""
        assert _PII_VARIABLE_NAMES, (
            "XZ-LOG-12: ``_PII_VARIABLE_NAMES`` is empty, the regression guard would not catch any unsafe log calls."
        )

    def test_safe_log_call_sites_allowlist_uses_unique_snippets(self) -> None:
        """Sanity check: each allowlist entry is unique within its file."""
        for file_name, snippets in _SAFE_LOG_CALL_SITES.items():
            assert len(snippets) == len(set(snippets)), (
                f"XZ-LOG-12: ``_SAFE_LOG_CALL_SITES['{file_name}']`` has "
                f"duplicate entries, each allowlist snippet must be unique."
            )
