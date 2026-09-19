"""XZ-LOG-12: regression guard against raw-transcription-text logging."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from voice_typer.server import dictation_pipeline

_RAW_TEXT_VARIABLES = ("text", "transcript", "partial", "final_text", "result")

# Log method names whose first positional arg after the format string
_LOG_METHODS = ("debug", "info", "warning", "error", "exception", "critical", "log")


def _is_raw_text_arg(arg: ast.expr) -> bool:
    """raw-transcription-text variables (e.g. ``text``, ``partial``)."""
    # Bare Name node matching one of the raw-text variables.
    return bool(isinstance(arg, ast.Name) and arg.id in _RAW_TEXT_VARIABLES)


def _collect_offending_log_calls(source: str, filename: str) -> list[str]:
    """Walk the module AST and return a list of human-readable"""
    tree = ast.parse(source, filename=filename)
    offenders: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            # Match ``log.<level>(...)`` calls, i.e. an Attribute
            if isinstance(node.func, ast.Attribute) and node.func.attr in _LOG_METHODS:  # noqa: SIM102
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "log":
                    args = node.args
                    if node.func.attr == "log":  # noqa: SIM108
                        format_args = args[2:]
                    else:
                        # ``log.<level>(fmt, *args)``, skip the first
                        format_args = args[1:]
                    for arg in format_args:
                        if _is_raw_text_arg(arg):
                            offenders.append(
                                f"{filename}:{node.lineno}: log.{node.func.attr}(...) "
                                f"interpolates raw text variable `{ast.unparse(arg)}` "
                                f"as a format arg, wrap with redact_pii/len/hash instead."
                            )
            self.generic_visit(node)

    _Visitor().visit(tree)
    return offenders


class TestNoRawTranscriptionTextInLogCalls:
    """XZ-LOG-12: ``dictation_pipeline`` package modules must NEVER"""

    def test_no_raw_text_interpolation_in_dictation_pipeline(self) -> None:
        # Walk every ``.py`` file in the ``dictation_pipeline`` package
        pkg_dir = Path(dictation_pipeline.__file__).parent
        package_sources: list[tuple[str, str]] = []
        for py_file in sorted(pkg_dir.glob("*.py")):
            package_sources.append((py_file.read_text(encoding="utf-8"), str(py_file)))

        assert len(package_sources) >= 8, (
            f"Expected at least 8 .py files in the dictation_pipeline package "
            f"(__init__ + 7 mixins/helpers); got {len(package_sources)} at {pkg_dir}. "
            f"The XZ-LOG-12 scan would silently pass if the glob is empty."
        )

        offenders: list[str] = []
        for source, filename in package_sources:
            offenders.extend(_collect_offending_log_calls(source, filename))
        assert not offenders, (
            "XZ-LOG-12: the dictation_pipeline package contains log.<level>(...) calls "
            "that interpolate a raw transcription-text variable as a format "
            "argument. This leaks the user's dictated content into "
            "voice-typer.log. Wrap the variable with `redact_pii(...)`, "
            "log only `len(text)` / a SHA-256 prefix, or omit it. "
            "Offenders:\n  " + "\n  ".join(offenders)
        )

    def test_no_raw_text_interpolation_in_module_globals(self) -> None:
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


def test_dictation_pipeline_source_is_readable() -> None:
    """Guard against the test silently passing because"""
    source = inspect.getsource(dictation_pipeline)
    assert "class DictationPipeline" in source, (
        "Could not find `class DictationPipeline` in the source, inspect.getsource may have returned the wrong module."
    )


def test_raw_text_variables_tuple_is_nonempty() -> None:
    """If someone accidentally empties ``_RAW_TEXT_VARIABLES``, the"""
    assert _RAW_TEXT_VARIABLES, "_RAW_TEXT_VARIABLES must be non-empty, otherwise the regression test silently passes."
    assert "text" in _RAW_TEXT_VARIABLES, (
        "'text' MUST be in the raw-text-variables tuple, it's the "
        "canonical name for the transcription text throughout the "
        "pipeline."
    )


def test_source_file_path_exists() -> None:
    """The test reads source via ``inspect.getsource``; pin the file"""
    src_path = Path(dictation_pipeline.__file__)
    assert src_path.is_file(), f"dictation_pipeline source not found at {src_path}"
