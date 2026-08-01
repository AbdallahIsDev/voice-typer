"""XZ-LOG-12: regression guard against raw-transcription-text logging.

Background
----------
``PIIRedactionFilter`` in ``voice_typer/server/security.py`` redacts
structured PII patterns (email / phone / SSN / CC) but does NOT redact
free-form transcription text. The convention — enforced at
``dictation_pipeline.py:1452-1474`` — is that the transcription text
itself is NEVER interpolated directly into a ``log.<level>(...)`` call;
only a non-reversible SHA-256 prefix (12 chars) and the text length
are logged, so an operator can correlate cycle IDs across log lines
without ever seeing the user's dictated content.

Risk
----
A future regression that adds ``log.info("[TRANSCRIBE] text=%s", text)``
or ``log.debug("partial=%s", partial)`` would leak the user's
dictation (medical / financial / personal) into ``voice-typer.log``,
which is included in diagnostics exports.

This test
---------
Greps the ``dictation_pipeline.py`` source for ``log.<level>(...)``
calls that interpolate any of the known transcription-text variable
names (``text``, ``transcript``, ``partial``, ``final_text``,
``result``) as a FORMAT ARGUMENT (i.e. ``%s`` substitution). The
length / hash / redacted-proxy forms (``len(text)``,
``text_hash``, ``redact_pii(text)``, ``text[:N]`` inside an
``event_bus.publish`` payload) are allowed — only the bare-variable
interpolation is rejected.

CONTRIBUTING.md should also document this convention — that's a
cross-file follow-up owned by the docs agent.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from voice_typer.server import dictation_pipeline

# Variable names that hold raw transcription text at various points in
# the pipeline. Adding a new one requires updating this tuple AND
# verifying the new variable is never logged directly.
_RAW_TEXT_VARIABLES = ("text", "transcript", "partial", "final_text", "result")

# Log method names whose first positional arg after the format string
# is a format-arg list. ``log.exception`` and ``log.error`` are
# included because they also accept a format string + args.
_LOG_METHODS = ("debug", "info", "warning", "error", "exception", "critical", "log")


def _is_raw_text_arg(arg: ast.expr) -> bool:
    """Return True iff ``arg`` is a bare-name reference to one of the
    raw-transcription-text variables (e.g. ``text``, ``partial``).

    Allowed (NOT flagged):
      - ``len(text)``                     — Call with Name as arg
      - ``text_hash``                     — different name (not in the tuple)
      - ``redact_pii(text)``              — Call wrapping the name
      - ``text[:200]``                    — Subscript
      - ``f"...{text}..."``               — JoinedStr (different node type)
      - ``"literal " + text``             — BinOp
      - ``str(text)``                     — Call wrapping the name
      - string literals / numbers / None  — Constant
    """
    # Bare Name node matching one of the raw-text variables.
    return bool(isinstance(arg, ast.Name) and arg.id in _RAW_TEXT_VARIABLES)


def _collect_offending_log_calls(source: str, filename: str) -> list[str]:
    """Walk the module AST and return a list of human-readable
    descriptions of every ``log.<level>(...)`` call that interpolates a
    raw-text variable as a format argument."""
    tree = ast.parse(source, filename=filename)
    offenders: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            # Match ``log.<level>(...)`` calls — i.e. an Attribute
            # access on a Name ``log`` whose ``.attr`` is one of the
            # known log method names.
            if isinstance(node.func, ast.Attribute) and node.func.attr in _LOG_METHODS:  # noqa: SIM102
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "log":
                    # First positional arg is the format string; the
                    # rest are format-args. Walk each format-arg and
                    # flag bare-name references to raw-text variables.
                    # ``log.log(level, msg, *args)`` has the level as
                    # the first positional arg, so we skip the first
                    # two positionals in that case.
                    args = node.args
                    if node.func.attr == "log":  # noqa: SIM108
                        # ``log.log(level, fmt, *args)`` — skip the
                        # first TWO positionals (level + fmt).
                        format_args = args[2:]
                    else:
                        # ``log.<level>(fmt, *args)`` — skip the first
                        # positional (the format string).
                        format_args = args[1:]
                    for arg in format_args:
                        if _is_raw_text_arg(arg):
                            offenders.append(
                                f"{filename}:{node.lineno}: log.{node.func.attr}(...) "
                                f"interpolates raw text variable `{ast.unparse(arg)}` "
                                f"as a format arg — wrap with redact_pii/len/hash instead."
                            )
            self.generic_visit(node)

    _Visitor().visit(tree)
    return offenders


class TestNoRawTranscriptionTextInLogCalls:
    """XZ-LOG-12: ``dictation_pipeline.py`` must NEVER interpolate raw
    transcription text (``text`` / ``transcript`` / ``partial`` /
    ``final_text`` / ``result``) as a format argument to a
    ``log.<level>(...)`` call. The convention is enforced at the AST
    level so a string-typing regression (e.g. ``log.info("got: %s",
    text)``) is caught at test time, not after a user's medical
    dictation lands in ``voice-typer.log``.
    """

    def test_no_raw_text_interpolation_in_dictation_pipeline(self) -> None:
        source = inspect.getsource(dictation_pipeline)
        offenders = _collect_offending_log_calls(source, dictation_pipeline.__file__)
        assert not offenders, (
            "XZ-LOG-12: dictation_pipeline.py contains log.<level>(...) calls "
            "that interpolate a raw transcription-text variable as a format "
            "argument. This leaks the user's dictated content into "
            "voice-typer.log. Wrap the variable with `redact_pii(...)`, "
            "log only `len(text)` / a SHA-256 prefix, or omit it. "
            "Offenders:\n  " + "\n  ".join(offenders)
        )

    def test_no_raw_text_interpolation_in_module_globals(self) -> None:
        # Sanity-check the helper against a synthetic module that DOES
        # interpolate a raw text variable. This pins the test's
        # detection capability so a future contributor can't accidentally
        # weaken the regex/AST walker into a no-op.
        bad_source = (
            "import logging\n"
            "log = logging.getLogger(__name__)\n"
            "def f(text: str) -> None:\n"
            "    log.info('got: %s', text)\n"  # BAD: bare-name interpolation
            "    log.debug('len=%d', len(text))\n"  # OK: len(text)
            "    log.warning('hash=%s', hash(text))\n"  # OK: hash(text)
        )
        offenders = _collect_offending_log_calls(bad_source, "<synthetic>")
        assert len(offenders) == 1, (
            "Sanity check failed: the helper should flag exactly one "
            "offender (the bare `text` interpolation) in the synthetic "
            "source. Got: " + repr(offenders)
        )
        assert "log.info" in offenders[0]
        assert "`text`" in offenders[0]


# ─── Wire check: ensure the module path is what we think it is ──────────


def test_dictation_pipeline_source_is_readable() -> None:
    """Guard against the test silently passing because
    ``inspect.getsource`` returned an empty string (e.g. if the module
    was loaded from a compiled .pyc with no source available)."""
    source = inspect.getsource(dictation_pipeline)
    assert "class DictationPipeline" in source, (
        "Could not find `class DictationPipeline` in the source — inspect.getsource may have returned the wrong module."
    )


def test_raw_text_variables_tuple_is_nonempty() -> None:
    """If someone accidentally empties ``_RAW_TEXT_VARIABLES``, the
    offender-grep becomes a no-op. Pin the tuple to a non-empty set."""
    assert _RAW_TEXT_VARIABLES, "_RAW_TEXT_VARIABLES must be non-empty — otherwise the regression test silently passes."
    assert "text" in _RAW_TEXT_VARIABLES, (
        "'text' MUST be in the raw-text-variables tuple — it's the "
        "canonical name for the transcription text throughout the "
        "pipeline."
    )


def test_source_file_path_exists() -> None:
    """The test reads source via ``inspect.getsource``; pin the file
    path so a future module rename doesn't silently break the test."""
    src_path = Path(dictation_pipeline.__file__)
    assert src_path.is_file(), f"dictation_pipeline source not found at {src_path}"
