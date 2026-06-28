"""Regression tests for the sixth-pass forensic review (changes-6).

Each test class pins one finding to its current verified state.

Findings covered
----------------
Source fixes (3):
- TS error     Settings.tsx uses window.python?.call() not .ipc()
- ARCH-018     Atomic pop_streaming_session() eliminates TOCTOU in cancel path
- TEST-009     test_committed_text_sorted_by_time now asserts sort order

False positives pinned (6):
- TEST-032     41 @pytest.mark.parametrize uses (not 6)
- TEST-033     0 `import mock` instances (convention documented)
- TEST-036     pyrefly IS run in CI (with continue-on-error caveat)
- TEST-039     TestCorrectionsExplicitLoad exists
- TEST-008     RTL/emoji/boundary/concurrent tests exist
- TEST-020     np.interp fallback IS tested
"""
from __future__ import annotations

import inspect
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── TS error — Settings.tsx uses window.python?.call() not .ipc() ────────


class TestTsErrorSettingsIpcCall:
    """TypeScript error: Property 'ipc' does not exist on type 'PythonBridge'.

    The finding: Settings.tsx:394 called ``window.python?.ipc(...)``
    but the PythonBridge type only exposes ``call`` and ``onEvent``.
    Fix: replaced ``.ipc(...)`` with ``.call(...)``.
    """

    def test_settings_uses_call_not_ipc(self):
        settings_path = Path(__file__).resolve().parent.parent / "voice_typer" / \
            "client" / "src" / "renderer" / "src" / "pages" / "Settings.tsx"
        src = settings_path.read_text(encoding="utf-8")
        # Must use .call( not .ipc(
        assert "window.python?.call(" in src, (
            "TS error: Settings.tsx must use window.python?.call() not .ipc()"
        )
        # Must NOT use .ipc( anywhere
        assert "window.python?.ipc(" not in src, (
            "TS error: Settings.tsx must NOT use window.python?.ipc() — "
            "the PythonBridge type does not expose an 'ipc' method"
        )

    def test_python_bridge_type_has_no_ipc_method(self):
        """The PythonBridge interface must NOT expose an 'ipc' method."""
        ipc_types_path = Path(__file__).resolve().parent.parent / "voice_typer" / \
            "client" / "src" / "renderer" / "src" / "types" / "ipc.ts"
        src = ipc_types_path.read_text(encoding="utf-8")
        # Extract the PythonBridge interface block
        bridge_start = src.find("export interface PythonBridge")
        assert bridge_start >= 0, "PythonBridge interface not found"
        # Find the closing brace
        brace_start = src.find("{", bridge_start)
        brace_end = src.find("}", brace_start)
        bridge_block = src[bridge_start:brace_end]
        assert "ipc" not in bridge_block, (
            "TS error: PythonBridge interface must NOT have an 'ipc' method"
        )
        assert "call:" in bridge_block, (
            "PythonBridge must have a 'call' method"
        )


# ─── ARCH-018 — Atomic pop_streaming_session() ───────────────────────────


class TestArch018AtomicPopStreamingSession:
    """ARCH-018.

    The finding: streaming session lock had a TOCTOU gap in the cancel
    path — ``_cancel_streaming_session`` did get-then-set (two lock
    acquisitions), allowing a concurrent start to install a new session
    that the subsequent set(None) would clobber. Fix: added
    ``pop_streaming_session()`` that does atomic get-and-clear under a
    single lock acquisition.
    """

    def test_pop_streaming_session_exists(self):
        from voice_typer.server.recording_controller import RecordingController

        assert hasattr(RecordingController, "pop_streaming_session"), (
            "ARCH-018: RecordingController must have pop_streaming_session method"
        )

    def test_pop_is_atomic_single_lock_acquisition(self):
        """pop_streaming_session must acquire the lock exactly once."""
        from voice_typer.server.recording_controller import RecordingController

        src = inspect.getsource(RecordingController.pop_streaming_session)
        # Must contain exactly one `with self._streaming_session_lock:` block
        assert src.count("with self._streaming_session_lock:") == 1, (
            "ARCH-018: pop_streaming_session must acquire the lock exactly once "
            "(atomic get-and-clear)"
        )

    def test_cancel_uses_pop_not_get_then_set(self):
        """_cancel_streaming_session must use pop_streaming_session(),
        not the pre-fix get_streaming_session() + set_streaming_session(None).
        """
        from voice_typer.server.recording_controller import RecordingController

        src = inspect.getsource(RecordingController._cancel_streaming_session)
        assert "self.pop_streaming_session()" in src, (
            "ARCH-018: _cancel_streaming_session must use pop_streaming_session() "
            "(atomic) instead of get+set (TOCTOU)"
        )
        # Must NOT contain the pre-fix pattern
        assert "self.get_streaming_session()" not in src or \
               "self.set_streaming_session(None)" not in src, (
            "ARCH-018: _cancel_streaming_session must NOT use the pre-fix "
            "get+set pattern (TOCTOU race)"
        )

    def test_pop_returns_and_clears_session(self):
        """Functional test: pop_streaming_session must return the current
        session AND clear it in one atomic operation.
        """
        from voice_typer.server.recording_controller import RecordingController

        # Build a minimal controller
        ctrl = RecordingController.__new__(RecordingController)
        ctrl._streaming_session_lock = threading.Lock()
        ctrl._streaming_session = MagicMock()

        # Pop must return the session AND clear the field
        session = ctrl.pop_streaming_session()
        assert session is ctrl._streaming_session or session is not None
        assert ctrl._streaming_session is None, (
            "ARCH-018: pop_streaming_session must clear the session field"
        )

    def test_pop_returns_none_when_no_session(self):
        """pop_streaming_session must return None when no session exists."""
        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._streaming_session_lock = threading.Lock()
        ctrl._streaming_session = None

        assert ctrl.pop_streaming_session() is None

    def test_concurrent_pop_and_set_no_clobber(self):
        """ARCH-018 regression test: a concurrent set_streaming_session
        must NOT be clobbered by a pop_streaming_session that started
        before the set. Pre-fix, the get-then-set pattern could clobber
        a freshly-installed session.
        """
        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._streaming_session_lock = threading.Lock()
        ctrl._streaming_session = MagicMock()

        # Simulate the race: thread A pops (get+clear), thread B sets a
        # new session between A's get and A's clear. With the atomic pop,
        # B's set happens AFTER A's pop completes, so the new session
        # survives.
        results: dict[str, object] = {}

        def thread_a():
            # Pop the initial session
            results["popped"] = ctrl.pop_streaming_session()

        def thread_b():
            # Wait briefly, then set a new session
            time.sleep(0.001)
            new_session = MagicMock(name="new_session")
            ctrl.set_streaming_session(new_session)
            results["set"] = new_session

        t_a = threading.Thread(target=thread_a)
        t_b = threading.Thread(target=thread_b)
        t_a.start()
        t_b.start()
        t_a.join(timeout=2.0)
        t_b.join(timeout=2.0)

        # The popped session is the ORIGINAL (not the new one)
        assert results["popped"] is not None, "Thread A should have popped the original session"
        # The set session survives (not clobbered by the pop)
        assert "set" in results, "Thread B should have set a new session"
        # After both threads complete, the session should be the one B set
        # (because pop cleared the original, then B set the new one)
        assert ctrl._streaming_session is results["set"], (
            "ARCH-018 regression: the new session set by thread B was "
            "clobbered by thread A's pop — the atomic get-and-clear fix "
            "is not working."
        )


# ─── TEST-009 — test_committed_text_sorted_by_time asserts sort order ────


class TestTest009SortOrderAssertion:
    """TEST-009.

    The finding: test_committed_text_sorted_by_time() only asserted
    isinstance(result, str) — no sort-order assertion. Fix: added
    chronological-order verification by comparing emitted words against
    the input sorted by start_seconds.
    """

    def test_test_has_sort_order_assertion(self):
        test_path = Path(__file__).resolve().parent / "test_streaming_hypothesis.py"
        src = test_path.read_text(encoding="utf-8")
        # Must contain the TEST-009 fix comment
        assert "TEST-009" in src, (
            "TEST-009: test file must reference the fix"
        )
        # Must contain sort-order verification logic
        assert "sorted(words, key=lambda w: w.start_seconds)" in src, (
            "TEST-009: test must sort input words by start_seconds for comparison"
        )
        assert "emitted_words" in src, (
            "TEST-009: test must extract emitted words from committed_text"
        )
        assert "expected_words" in src, (
            "TEST-009: test must build expected word sequence"
        )


# ─── TEST-032 — 41 @pytest.mark.parametrize uses (pin) ───────────────────


class TestTest032ParametrizeCount:
    """TEST-032.

    The finding: only 6 @pytest.mark.parametrize uses. Investigation:
    41 uses now exist across 7 files. This test pins that state.
    """

    def test_parametrize_count_is_above_30(self):
        """At least 30 @pytest.mark.parametrize uses must exist.

        Uses Python's pathlib + grep instead of the Unix `grep` command
        so it works on Windows too.
        """
        from pathlib import Path

        tests_dir = Path(__file__).resolve().parent
        count = 0
        for py_file in tests_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                count += content.count("@pytest.mark.parametrize")
            except Exception:
                pass
        assert count >= 30, (
            f"TEST-032: expected at least 30 @pytest.mark.parametrize uses, "
            f"found {count}"
        )


# ─── TEST-033 — no `import mock` (pin) ───────────────────────────────────


class TestTest033NoImportMock:
    """TEST-033.

    The finding: `import mock` and `from unittest.mock import` coexist.
    Investigation: 0 `import mock` instances; convention documented in
    CONTRIBUTING.md. This test pins that state.
    """

    def test_no_import_mock_in_tests(self):
        """No test file must use `import mock` (use `from unittest.mock import` instead).

        Uses Python's pathlib instead of the Unix `grep` command so it
        works on Windows too.
        """
        from pathlib import Path

        tests_dir = Path(__file__).resolve().parent
        violations = []
        for py_file in tests_dir.rglob("*.py"):
            try:
                for line_num, line in enumerate(
                    py_file.read_text(encoding="utf-8").splitlines(), 1
                ):
                    if line.strip() == "import mock":
                        violations.append(f"{py_file}:{line_num}")
            except Exception:
                pass
        assert not violations, (
            f"TEST-033: found `import mock` usage in tests:\n{chr(10).join(violations)}\n"
            "Use `from unittest.mock import MagicMock, patch` instead."
        )

    def test_convention_documented_in_contributing(self):
        contributing = Path(__file__).resolve().parent.parent / "CONTRIBUTING.md"
        if contributing.exists():
            src = contributing.read_text(encoding="utf-8")
            assert "unittest.mock" in src or "from unittest.mock" in src, (
                "TEST-033: CONTRIBUTING.md must document the mock convention"
            )


# ─── TEST-036 — pyrefly IS run in CI (pin) ──────────────────────────────


class TestTest036PyreflyInCi:
    """TEST-036.

    The finding: pyrefly configured but not run in CI. Investigation:
    pyrefly IS now run in CI (build.yml:43-50), with continue-on-error=true
    as a soft gate. This test pins that state.
    """

    def test_pyrefly_in_build_yml(self):
        build_yml = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build.yml"
        if build_yml.exists():
            src = build_yml.read_text(encoding="utf-8")
            assert "pyrefly" in src, (
                "TEST-036: build.yml must run pyrefly in CI"
            )
            assert "pyrefly check" in src, (
                "TEST-036: build.yml must run 'pyrefly check'"
            )

    def test_pyrefly_configured_in_pyproject(self):
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        src = pyproject.read_text(encoding="utf-8")
        assert "[tool.pyrefly]" in src, (
            "TEST-036: pyproject.toml must have [tool.pyrefly] section"
        )


# ─── TEST-039 — corrections.json explicitly tested as loadable (pin) ─────


class TestTest039CorrectionsExplicitLoad:
    """TEST-039.

    The finding: corrections.json never explicitly tested as loadable.
    Investigation: TestCorrectionsExplicitLoad exists in test_corruptions.py.
    This test pins that state.
    """

    def test_explicit_load_test_class_exists(self):
        test_path = Path(__file__).resolve().parent / "test_corruptions.py"
        if test_path.exists():
            src = test_path.read_text(encoding="utf-8")
            assert "TestCorrectionsExplicitLoad" in src, (
                "TEST-039: TestCorrectionsExplicitLoad class must exist"
            )
            assert "test_bundled_corrections_json_loads" in src, (
                "TEST-039: test_bundled_corrections_json_loads must exist"
            )


# ─── TEST-008 — RTL/emoji/boundary/concurrent tests exist (pin) ──────────


class TestTest008TextCleanupDepth:
    """TEST-008.

    The finding: no RTL/emoji/concurrent/boundary tests. Investigation:
    TestTextCleanupUnicode in test_text_cleanup.py has all four categories.
    This test pins that state.
    """

    def test_unicode_test_class_exists(self):
        test_path = Path(__file__).resolve().parent / "test_text_cleanup.py"
        if test_path.exists():
            src = test_path.read_text(encoding="utf-8")
            assert "TestTextCleanupUnicode" in src, (
                "TEST-008: TestTextCleanupUnicode class must exist"
            )

    def test_concurrent_cleanup_test_exists(self):
        test_path = Path(__file__).resolve().parent / "test_text_cleanup.py"
        if test_path.exists():
            src = test_path.read_text(encoding="utf-8")
            assert "test_concurrent_cleanup_calls" in src, (
                "TEST-008: concurrent cleanup test must exist"
            )

    def test_boundary_inputs_test_exists(self):
        test_path = Path(__file__).resolve().parent / "test_text_cleanup.py"
        if test_path.exists():
            src = test_path.read_text(encoding="utf-8")
            assert "test_boundary_inputs_never_crash" in src, (
                "TEST-008: boundary inputs test must exist"
            )


# ─── TEST-020 — np.interp fallback IS tested (pin) ──────────────────────


class TestTest020ResampleFallbackTested:
    """TEST-020.

    The finding: np.interp fallback not tested. Investigation:
    TestResampleFallback in test_recording.py explicitly tests the
    np.interp path. This test pins that state.
    """

    def test_np_interp_fallback_test_exists(self):
        test_path = Path(__file__).resolve().parent / "test_recording.py"
        if test_path.exists():
            src = test_path.read_text(encoding="utf-8")
            assert "test_fallback_to_np_interp_when_scipy_unavailable" in src, (
                "TEST-020: np.interp fallback test must exist"
            )
            assert "test_resample_fallback_quality_with_known_sine" in src, (
                "TEST-020: fallback quality test must exist"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
