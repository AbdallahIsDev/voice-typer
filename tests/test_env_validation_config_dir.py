"""XZ-14-07: VOICE_TYPER_CONFIG_DIR path-safety validation tests.

Dedicated regression suite for the fix that mirrors the
SEC-HFHOME-001 (HIGH-12) pattern in :mod:`voice_typer.server.env_validation`:
``VOICE_TYPER_CONFIG_DIR`` now runs
``_validate_path_safety(Path(val), Path.home())`` AFTER the basic
``_path_pattern`` (NUL byte) + length check, so attacker-controlled
values that escape the user's home directory via ``..`` or absolute
paths outside home are rejected at the env-var entry point — defense
in depth on top of the same check that ``config._config_dir()``
already performs on the consumer side.

Scope (this file only):
  * VOICE_TYPER_CONFIG_DIR-specific path-safety behaviour.
  * The HF_HOME equivalent is covered by ``TestGtB1_14PathSafetyExceptionType``
    in ``tests/test_env_validation.py`` — not duplicated here.

The tests use the REAL ``_validate_path_safety`` wherever possible
(deterministic absolute paths outside home, or chdir to a temp dir
so ``../../etc/passwd`` resolves outside home) so the test exercises
the actual containment predicate. ``unittest.mock`` / ``monkeypatch``
is used only when the test needs to assert on a specific exception
type or message (mirroring the HF_HOME exception-type test pattern).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from voice_typer.server.env_validation import _validate_env_vars

# All env vars touched by _validate_env_vars (kept in sync with the SUT
# so the autouse fixture can wipe them between tests).
_BOOL_VARS = (
    "VOICE_TYPER_QUIET",
    "VOICE_TYPER_DEBUG",
    "VOICE_TYPER_NO_TRAY",
    "VOICE_TYPER_STREAMING",
)
_TOKEN_VARS = ("VOICE_TYPER_RESTART", "VOICE_TYPER_IPC_TOKEN")
_PATH_VARS = ("VOICE_TYPER_CONFIG_DIR", "HF_HOME")
_ALL_VARS = _BOOL_VARS + _TOKEN_VARS + _PATH_VARS + ("HF_ENDPOINT",)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure none of the validated vars pre-exist before each test.

    Mirrors the ``_clean_env`` fixture in ``tests/test_env_validation.py``
    so a CI-shell leak of ``HF_HOME`` / ``VOICE_TYPER_DEBUG`` cannot
    contaminate these tests.
    """
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


# ─── Real path-safety predicate: traversal paths rejected ─────────────


class TestConfigDirTraversalRejected:
    """XZ-14-07: VOICE_TYPER_CONFIG_DIR with a path that escapes
    ``Path.home()`` MUST be popped from ``os.environ`` after
    ``_validate_env_vars()`` runs.

    These tests use the REAL ``_validate_path_safety`` (no mocking) so
    they exercise the actual ``_is_path_within`` /
    ``os.path.commonpath`` containment predicate from
    :mod:`voice_typer.server.config`.
    """

    def test_absolute_path_outside_home_rejected(self, monkeypatch):
        """An absolute path outside ``Path.home()`` (``/etc/passwd``)
        is rejected by ``_validate_path_safety`` and popped."""
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", "/etc/passwd")
        _validate_env_vars()
        assert "VOICE_TYPER_CONFIG_DIR" not in os.environ

    def test_traversal_path_rejected_under_chdir(self, monkeypatch, tmp_path):
        """The classic ``../../etc/passwd`` traversal pattern is
        rejected when ``cwd`` is a tmp dir under ``/tmp`` (so the
        resolved path escapes ``Path.home()``).

        This mirrors the documented test case in the XZ-14-07 fix
        brief: "Write a test that verifies VOICE_TYPER_CONFIG_DIR with
        a traversal path (e.g., '../../etc/passwd') is rejected."

        From ``tmp_path`` (under ``/tmp/pytest-of-<user>/...``),
        ``Path('../../etc/passwd').resolve()`` lands under ``/tmp/...``
        which is NOT under ``Path.home()`` (``/home/<user>``), so
        ``_validate_path_safety`` raises ``ValueError``.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", "../../etc/passwd")
        _validate_env_vars()
        assert "VOICE_TYPER_CONFIG_DIR" not in os.environ

    def test_traversal_path_rejected_logs_warning(self, monkeypatch, tmp_path, caplog):
        """When the traversal is rejected, a WARNING is logged that
        mentions ``VOICE_TYPER_CONFIG_DIR`` and the path-safety rule
        (so the operator can diagnose without grepping source)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", "../../etc/passwd")
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        matching = [
            r for r in caplog.records if "VOICE_TYPER_CONFIG_DIR" in r.getMessage() and "path-safety" in r.getMessage()
        ]
        assert matching, (
            "expected a path-safety WARNING mentioning "
            f"VOICE_TYPER_CONFIG_DIR; got "
            f"{[r.getMessage() for r in caplog.records]!r}"
        )

    def test_traversal_rejection_uses_redacted_placeholder(self, monkeypatch, tmp_path, caplog):
        """GT-63 (call-site redaction): the WARNING message MUST
        contain the literal ``<redacted>`` placeholder (proving the
        SUT pre-redacts the env-var value at the call site, mirroring
        the HF_HOME pattern).

        Note: ``_validate_path_safety`` in ``config.py`` itself embeds
        the raw path in its ``ValueError`` message (``"Path traversal
        detected: <path> escapes <parent>"``), which is logged via
        ``%s: %s`` of the exception instance — that predicate-side
        leak is shared by both the HF_HOME and VOICE_TYPER_CONFIG_DIR
        blocks and is out of scope for XZ-14-07 (it's owned by the
        config.py maintainer). The GT-63 contract for the SUT call
        site is verified separately via a mocked predicate in
        :class:`TestConfigDirPathSafetyExceptionType`.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", "../../etc/passwd")
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        rendered = [r.getMessage() for r in caplog.records]
        assert any("<redacted>" in m and "VOICE_TYPER_CONFIG_DIR" in m for m in rendered), (
            f"expected a redacted WARNING mentioning VOICE_TYPER_CONFIG_DIR; got {rendered!r}"
        )

    def test_absolute_path_outside_home_logs_warning(self, monkeypatch, caplog):
        """Companion to the ``/etc/passwd`` rejection test — verify
        the WARNING is emitted (not just the env-var pop)."""
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", "/etc/passwd")
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        matching = [
            r for r in caplog.records if "VOICE_TYPER_CONFIG_DIR" in r.getMessage() and "path-safety" in r.getMessage()
        ]
        assert matching, (
            f"expected a path-safety WARNING for /etc/passwd; got {[r.getMessage() for r in caplog.records]!r}"
        )


# ─── Real path-safety predicate: in-home paths preserved ─────────────


class TestConfigDirInHomePreserved:
    """XZ-14-07: VOICE_TYPER_CONFIG_DIR with a path INSIDE
    ``Path.home()`` is preserved by ``_validate_path_safety`` (no
    false-positive rejection)."""

    def test_in_home_path_preserved(self, monkeypatch):
        """A path that resolves to a descendant of ``Path.home()`` is
        preserved verbatim by the validator."""
        safe_path = str(Path.home() / ".config" / "voice-typer")
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", safe_path)
        _validate_env_vars()
        assert os.environ.get("VOICE_TYPER_CONFIG_DIR") == safe_path

    def test_relative_in_home_path_preserved(self, monkeypatch, tmp_path):
        """A relative path that resolves under ``Path.home()`` (e.g.
        a subdirectory of the current working directory, when cwd is
        itself under home) is preserved."""
        # tmp_path is typically /tmp/pytest-of-<user>/... which is NOT
        # under Path.home(); chdir to a sub-dir of home instead.
        in_home_dir = Path.home() / ".voice-typer-test-cwd"
        in_home_dir.mkdir(exist_ok=True)
        monkeypatch.chdir(in_home_dir)
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", "subdir/config")
        _validate_env_vars()
        assert os.environ.get("VOICE_TYPER_CONFIG_DIR") == "subdir/config"

    def test_home_itself_preserved(self, monkeypatch):
        """``Path.home()`` itself is the parent — passing it as the
        config dir is accepted (a path IS within itself)."""
        safe_path = str(Path.home())
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", safe_path)
        _validate_env_vars()
        assert os.environ.get("VOICE_TYPER_CONFIG_DIR") == safe_path


# ─── Mocked _validate_path_safety: exception-type-name parity (GT-B1-14) ──


class TestConfigDirPathSafetyExceptionType:
    """XZ-14-07 / GT-B1-14 parity: when ``_validate_path_safety``
    rejects ``VOICE_TYPER_CONFIG_DIR``, the log message must include
    ``type(exc).__name__`` so the operator knows which validation
    predicate failed (``ValueError`` vs ``OSError`` vs
    ``RuntimeError``) without grepping source.

    Mirrors ``TestGtB1_14PathSafetyExceptionType`` in
    ``tests/test_env_validation.py`` (which covers the same contract
    for HF_HOME) — we mock ``_validate_path_safety`` so the test
    doesn't depend on cwd / home layout for raising a specific
    exception type.
    """

    def test_value_error_includes_type_name(self, monkeypatch, caplog):
        secret_path = "/tmp/some/path/that/escapes/home"
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", secret_path)

        def _raise_value_error(_path, _home):
            raise ValueError("path escapes home directory")

        monkeypatch.setattr(
            "voice_typer.server.config._validate_path_safety",
            _raise_value_error,
        )

        with caplog.at_level(logging.WARNING):
            _validate_env_vars()

        matching = [
            r for r in caplog.records if "VOICE_TYPER_CONFIG_DIR" in r.getMessage() and "path-safety" in r.getMessage()
        ]
        assert matching, (
            "expected a path-safety WARNING for VOICE_TYPER_CONFIG_DIR; "
            f"got {[r.getMessage() for r in caplog.records]!r}"
        )
        msg = matching[0].getMessage()
        assert "ValueError" in msg, f"GT-B1-14 regression: exception type name missing from log; got {msg!r}"
        assert "path escapes home directory" in msg
        assert secret_path not in msg, f"GT-63 regression: raw CONFIG_DIR path leaked: {msg!r}"
        assert "VOICE_TYPER_CONFIG_DIR" not in os.environ

    def test_oserror_includes_type_name(self, monkeypatch, caplog):
        """Same as above but with ``OSError`` to confirm the type name
        is dynamic, not hardcoded."""
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", "/tmp/escapes/home")

        def _raise_oserror(_path, _home):
            raise OSError("permission denied")

        monkeypatch.setattr(
            "voice_typer.server.config._validate_path_safety",
            _raise_oserror,
        )

        with caplog.at_level(logging.WARNING):
            _validate_env_vars()

        matching = [r for r in caplog.records if "path-safety" in r.getMessage()]
        assert matching, f"expected a path-safety WARNING; got {[r.getMessage() for r in caplog.records]!r}"
        msg = matching[0].getMessage()
        assert "OSError" in msg
        assert "permission denied" in msg
        assert "VOICE_TYPER_CONFIG_DIR" not in os.environ

    def test_runtimeerror_includes_type_name(self, monkeypatch, caplog):
        """The catch-all also covers ``RuntimeError`` (which
        ``Path.resolve`` can raise on some platforms for non-decodable
        paths). Verify the env var is popped and the type name is
        logged."""
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", "/tmp/escapes/home")

        def _raise_runtime_error(_path, _home):
            raise RuntimeError("undecodable path")

        monkeypatch.setattr(
            "voice_typer.server.config._validate_path_safety",
            _raise_runtime_error,
        )

        with caplog.at_level(logging.WARNING):
            _validate_env_vars()

        matching = [r for r in caplog.records if "path-safety" in r.getMessage()]
        assert matching, f"expected a path-safety WARNING; got {[r.getMessage() for r in caplog.records]!r}"
        msg = matching[0].getMessage()
        assert "RuntimeError" in msg
        assert "undecodable path" in msg
        assert "VOICE_TYPER_CONFIG_DIR" not in os.environ

    def test_other_exception_type_not_swallowed(self, monkeypatch):
        """The except clause catches only ``(ValueError, OSError,
        RuntimeError)``. A different exception type (e.g.
        ``TypeError``) must propagate so the operator sees the bug
        rather than silently dropping the env var."""
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", "/tmp/some/path")

        def _raise_type_error(_path, _home):
            raise TypeError("programmer error")

        monkeypatch.setattr(
            "voice_typer.server.config._validate_path_safety",
            _raise_type_error,
        )

        with pytest.raises(TypeError, match="programmer error"):
            _validate_env_vars()


# ─── Defense-in-depth: pattern check still runs first ──────────────────


class TestConfigDirPatternCheckStillRunsFirst:
    """XZ-14-07: the existing basic ``_path_pattern`` (NUL byte) +
    length (<= 4096) check still runs BEFORE the new
    ``_validate_path_safety`` call. The two checks are layered, not
    redundant — pattern failures must not reach the (heavier)
    path-safety code path.

    This is verified by mocking ``_validate_path_safety`` to RAISE if
    called; if the pattern check correctly short-circuits, the mock
    is never invoked.
    """

    def test_overlength_path_does_not_invoke_path_safety(self, monkeypatch):
        """An overlength path (>4096 chars) is rejected by the
        pattern check; ``_validate_path_safety`` must NOT be called
        (it would raise if called, failing the test)."""
        calls = []

        def _fail_if_called(_path, _home):
            calls.append("called")
            raise AssertionError("_validate_path_safety must not be called for pattern-check failures")

        monkeypatch.setattr(
            "voice_typer.server.config._validate_path_safety",
            _fail_if_called,
        )
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", "a" * 5000)
        _validate_env_vars()
        assert calls == [], "_validate_path_safety was invoked despite pattern failure"
        assert "VOICE_TYPER_CONFIG_DIR" not in os.environ

    def test_empty_path_does_not_invoke_path_safety(self, monkeypatch):
        """An empty string fails ``^[^\0]+$``; path-safety must not be
        called."""
        calls = []

        def _fail_if_called(_path, _home):
            calls.append("called")
            raise AssertionError("_validate_path_safety must not be called for pattern-check failures")

        monkeypatch.setattr(
            "voice_typer.server.config._validate_path_safety",
            _fail_if_called,
        )
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", "")
        _validate_env_vars()
        assert calls == []
        assert "VOICE_TYPER_CONFIG_DIR" not in os.environ

    def test_unset_var_does_not_invoke_path_safety(self, monkeypatch):
        """When ``VOICE_TYPER_CONFIG_DIR`` is unset, neither the
        pattern check nor the path-safety check runs."""
        calls = []

        def _fail_if_called(_path, _home):
            calls.append("called")
            raise AssertionError("_validate_path_safety must not be called when var is unset")

        monkeypatch.setattr(
            "voice_typer.server.config._validate_path_safety",
            _fail_if_called,
        )
        monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
        _validate_env_vars()
        assert calls == []
        assert "VOICE_TYPER_CONFIG_DIR" not in os.environ
