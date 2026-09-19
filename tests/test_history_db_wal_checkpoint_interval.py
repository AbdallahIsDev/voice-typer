"""Regression tests for the WAL checkpoint interval constant wiring."""

import inspect

import voice_typer.server.history_db as history_db_mod
from voice_typer.server.history_db_internals.writer import _run_checkpoint as _run_checkpoint_impl


def test_wal_checkpoint_interval_constant_exists_and_is_positive():
    """``_WAL_CHECKPOINT_INTERVAL`` must be a positive float seconds value."""
    assert hasattr(history_db_mod, "_WAL_CHECKPOINT_INTERVAL"), (
        "_WAL_CHECKPOINT_INTERVAL must be defined as a module-level constant in history_db.py: see XV-95."
    )
    value = history_db_mod._WAL_CHECKPOINT_INTERVAL
    assert isinstance(value, (int, float)), f"_WAL_CHECKPOINT_INTERVAL must be numeric, got {type(value)!r}."
    assert value > 0.0, f"_WAL_CHECKPOINT_INTERVAL must be positive, got {value!r}."


def test_wal_checkpoint_interval_constant_is_documented_in_neighbor_comment():
    """The constant's declaration must keep its inline ``# ...`` comment so"""
    src = inspect.getsource(history_db_mod)
    # Locate the line that declares the constant.
    decl_lines = [
        line
        for line in src.splitlines()
        if "_WAL_CHECKPOINT_INTERVAL" in line and "=" in line and "def " not in line and "self." not in line
    ]
    assert decl_lines, "Could not find the _WAL_CHECKPOINT_INTERVAL = <value> declaration in history_db source."
    decl = decl_lines[0]
    assert "#" in decl, (
        f"_WAL_CHECKPOINT_INTERVAL declaration must carry an inline comment naming the cadence; got: {decl!r}"
    )


def test_run_checkpoint_docstring_references_constant():
    """``_WAL_CHECKPOINT_INTERVAL`` constant, NOT a hardcoded numeric"""
    doc = _run_checkpoint_impl.__doc__ or ""
    assert "_WAL_CHECKPOINT_INTERVAL" in doc, (
        "_run_checkpoint docstring must reference the "
        "_WAL_CHECKPOINT_INTERVAL constant (XV-95 drift prevention). "
        f"Got docstring: {doc!r}"
    )
    # The docstring must NOT claim a hardcoded numeric cadence.
    for forbidden in ("60s", "60 seconds", "300s", "300 seconds"):
        assert forbidden not in doc.lower(), (
            f"_run_checkpoint docstring must not hardcode {forbidden!r} "
            f"(XV-95 drift); reference _WAL_CHECKPOINT_INTERVAL instead."
        )


def test_run_checkpoint_log_message_interpolates_constant():
    """The 'will retry in' log message must interpolate"""
    src = inspect.getsource(_run_checkpoint_impl)
    # The log call should mention the constant by name, not a literal.
    assert "_WAL_CHECKPOINT_INTERVAL" in src, (
        "_run_checkpoint source must reference "
        "_WAL_CHECKPOINT_INTERVAL (e.g. in the 'will retry in %.0fs' log "
        "call). XV-95: don't hardcode a numeric cadence."
    )
    lines = src.splitlines()
    log_lines = [i for i, ln in enumerate(lines) if "log." in ln]
    const_lines = [i for i, ln in enumerate(lines) if "_WAL_CHECKPOINT_INTERVAL" in ln]
    nearby = any(abs(li - ci) <= 3 for li in log_lines for ci in const_lines)
    assert nearby, (
        "_WAL_CHECKPOINT_INTERVAL must be referenced within the log-call "
        "argument region of _run_checkpoint so the message interpolates "
        "the constant instead of a hardcoded number (XV-95)."
    )


def test_run_checkpoint_comments_reference_constant_not_hardcoded():
    """checkpoint cadence must reference ``_WAL_CHECKPOINT_INTERVAL``"""
    src = inspect.getsource(_run_checkpoint_impl)
    # Find every cadence-flavored sentence in the comments and require
    for forbidden in ("300s", "60s"):
        assert forbidden not in src, (
            f"_run_checkpoint source must not contain the hardcoded "
            f"literal {forbidden!r}, reference _WAL_CHECKPOINT_INTERVAL "
            f"instead (XV-95 drift prevention)."
        )


def test_module_docstring_architecture_overview_references_constant():
    """The module-level architecture overview diagram in history_db.py"""
    module_doc = history_db_mod.__doc__ or ""
    assert "wal_checkpoint" in module_doc.lower() or "WAL_CHECKPOINT" in module_doc, (
        "Could not locate the WAL checkpoint mention in the module docstring's architecture overview."
    )
    assert "_WAL_CHECKPOINT_INTERVAL" in module_doc, (
        "Module docstring architecture overview must reference "
        "_WAL_CHECKPOINT_INTERVAL (XV-95 drift prevention). "
        "Hardcoded 'every 300s' would silently drift if the constant "
        "is bumped."
    )
    # The module docstring must not claim a wrong (60s) cadence.
    assert "every 60s" not in module_doc.lower(), (
        "Module docstring must not claim 'every 60s' for the WAL checkpoint cadence (XV-95: actual interval is 300s)."
    )
