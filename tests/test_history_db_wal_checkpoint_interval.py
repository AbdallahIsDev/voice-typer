"""Regression tests for the WAL checkpoint interval constant wiring.

XV-95 — history_db WAL checkpoint interval: the docstring/log must
reference the ``_WAL_CHECKPOINT_INTERVAL`` constant (NOT a hardcoded
``"60s"``/``"300s"`` literal). The original bug was a stale docstring
that still said ``60s`` after the interval was bumped to ``300s``.

These tests pin the contract by inspecting the source of:

* ``HistoryDB._run_checkpoint`` — docstring + log-message + comments
* the module docstring's architecture overview
* the comment block next to the constant definition

If a future refactor hardcodes a numeric cadence (e.g. re-introduces
``"60s"`` or hardcodes ``"300s"`` in the docstring/log/comments),
the corresponding assertion fails. This is a source-text contract
test — same pattern as the existing ``inspect.getsource`` tests
elsewhere in this suite.
"""

import inspect

import voice_typer.server.history_db as history_db_mod
from voice_typer.server.history_db_internals.writer import _run_checkpoint as _run_checkpoint_impl


def test_wal_checkpoint_interval_constant_exists_and_is_positive():
    """``_WAL_CHECKPOINT_INTERVAL`` must be a positive float seconds value."""
    assert hasattr(history_db_mod, "_WAL_CHECKPOINT_INTERVAL"), (
        "_WAL_CHECKPOINT_INTERVAL must be defined as a module-level constant in history_db.py — see XV-95."
    )
    value = history_db_mod._WAL_CHECKPOINT_INTERVAL
    assert isinstance(value, (int, float)), f"_WAL_CHECKPOINT_INTERVAL must be numeric, got {type(value)!r}."
    assert value > 0.0, f"_WAL_CHECKPOINT_INTERVAL must be positive, got {value!r}."


def test_wal_checkpoint_interval_constant_is_documented_in_neighbor_comment():
    """The constant's declaration must keep its inline ``# ...`` comment so
    a future reader knows what the numeric value means without scrolling."""
    src = inspect.getsource(history_db_mod)
    # Locate the line that declares the constant.
    decl_lines = [
        line
        for line in src.splitlines()
        if "_WAL_CHECKPOINT_INTERVAL" in line and "=" in line and "def " not in line and "self." not in line
    ]
    assert decl_lines, "Could not find the _WAL_CHECKPOINT_INTERVAL = <value> declaration in history_db source."
    decl = decl_lines[0]
    # The declaration itself must include a human-readable annotation (a
    # trailing comment naming the duration). A bare numeric literal with
    # no comment is what caused XV-95's drift in the first place.
    assert "#" in decl, (
        f"_WAL_CHECKPOINT_INTERVAL declaration must carry an inline comment naming the cadence; got: {decl!r}"
    )


def test_run_checkpoint_docstring_references_constant():
    """The ``_run_checkpoint`` implementation's docstring must reference the
    ``_WAL_CHECKPOINT_INTERVAL`` constant — NOT a hardcoded numeric
    cadence like ``60s`` or ``300s``.

    XV-95 root cause: a prior version of the docstring hardcoded
    ``"60s"`` after the interval was bumped to ``300s``. Pinning the
    constant reference here prevents the same drift from recurring.

    The implementation now lives in
    ``history_db_internals.writer._run_checkpoint`` (the class method is
    a thin delegate), so the source contract pins the implementation.
    """
    doc = _run_checkpoint_impl.__doc__ or ""
    assert "_WAL_CHECKPOINT_INTERVAL" in doc, (
        "_run_checkpoint docstring must reference the "
        "_WAL_CHECKPOINT_INTERVAL constant (XV-95 drift prevention). "
        f"Got docstring: {doc!r}"
    )
    # The docstring must NOT claim a hardcoded numeric cadence.
    # Match "60s"/"300s" only when adjacent to a checkpoint-cadence word.
    for forbidden in ("60s", "60 seconds", "300s", "300 seconds"):
        assert forbidden not in doc.lower(), (
            f"_run_checkpoint docstring must not hardcode {forbidden!r} "
            f"(XV-95 drift); reference _WAL_CHECKPOINT_INTERVAL instead."
        )


def test_run_checkpoint_log_message_interpolates_constant():
    """The 'will retry in' log message must interpolate
    ``_WAL_CHECKPOINT_INTERVAL`` rather than printing a hardcoded number.

    XV-95 also covered the log message: a stale hardcoded ``"60s"`` log
    string was misleading operators. The fix is to pass the constant
    itself as the log argument.
    """
    src = inspect.getsource(_run_checkpoint_impl)
    # The log call should mention the constant by name, not a literal.
    assert "_WAL_CHECKPOINT_INTERVAL" in src, (
        "_run_checkpoint source must reference "
        "_WAL_CHECKPOINT_INTERVAL (e.g. in the 'will retry in %.0fs' log "
        "call). XV-95: don't hardcode a numeric cadence."
    )
    # The OLD assertion pinned the exact log format string
    # ('"[HISTORY_DB] WAL checkpoint skipped (will retry in %.0fs): %s"'),
    # which made a wording tweak to the log message fail the drift guard
    # even though the constant was still interpolated. Relax it: the
    # constant must appear within a few lines of a ``log.`` call (its
    # argument region) without pinning the message text or the exact
    # line layout — so both multi-line and inlined log calls pass.
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
    """In-body comments inside ``_run_checkpoint`` that mention the
    checkpoint cadence must reference ``_WAL_CHECKPOINT_INTERVAL``
    rather than a hardcoded ``"300s"`` literal. Same drift-prevention
    rationale as XV-95: a future bump would silently leave the comments
    stale.
    """
    src = inspect.getsource(_run_checkpoint_impl)
    # Find every cadence-flavored sentence in the comments and require
    # that the constant name appears nearby. We approximate this by
    # forbidding any literal "300s" / "60s" inside the function body
    # (the docstring and log call already covered above).
    for forbidden in ("300s", "60s"):
        assert forbidden not in src, (
            f"_run_checkpoint source must not contain the hardcoded "
            f"literal {forbidden!r} — reference _WAL_CHECKPOINT_INTERVAL "
            f"instead (XV-95 drift prevention)."
        )


def test_module_docstring_architecture_overview_references_constant():
    """The module-level architecture overview diagram in history_db.py
    mentions the WAL checkpoint cadence. It must reference the
    ``_WAL_CHECKPOINT_INTERVAL`` constant (not a hardcoded ``"300s"``)
    so the diagram doesn't drift on the next interval bump (XV-95).
    """
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
