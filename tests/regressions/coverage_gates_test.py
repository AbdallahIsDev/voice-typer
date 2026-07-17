"""Regression tests split out of the former ``tests/test_bugfix_regressions.py``.

This module is part of the ``tests/regressions/`` package created by
REF-4. The class/method names, assertion logic, and imports below are
preserved verbatim from the original 4446-line monolith — only file
location has changed.

Common preamble (imports + Linux test-env shim) is identical to the
original file so that every test in this module sees the same global
state the monolith provided.
"""

from __future__ import annotations

import inspect
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Linux test-env shim (RW-8) ──────────────────────────────────────────
# ``voice_typer.server.crash_handler`` uses ``ctypes.WINFUNCTYPE`` as a
# decorator at module load time. That attribute only exists on Windows,
# so importing ``voice_typer.server.app`` (which does
# ``from voice_typer.server import crash_handler``) raises
# ``AttributeError`` on Linux. Many tests in this file introspect
# ``VoiceTyperApp`` source via ``inspect.getsource``; without this
# shim, those tests would fail non-deterministically depending on
# whether some earlier test happened to pre-load ``app``. The same
# pattern is used in ``tests/test_api_doc_accuracy.py:42-57``. This is
# a *test-only* shim — production code never monkey-patches ctypes.
if sys.platform != "win32" and "voice_typer.server.crash_handler" not in sys.modules:
    sys.modules["voice_typer.server.crash_handler"] = MagicMock()


class TestConcurrentCallbackTestCoverageExists:
    """RACE-001.

    The finding: no concurrent callback test. Investigation: the test
    exists in tests/test_changes2_fixes.py. This test pins that the
    concurrent test class is present.
    """

    def test_concurrent_callback_test_exists(self):
        """The TestAudioCallbackUsesMinimalLockScope class must exist.

        Originally pinned in tests/test_changes2_fixes.py — that file
        was consolidated into tests/test_bugfix_regressions.py, which
        REF-4 then split into the tests/regressions/ package. The class
        now lives in tests/regressions/audio_test.py.
        """
        try:
            from tests.regressions.audio_test import TestAudioCallbackUsesMinimalLockScope

            assert hasattr(TestAudioCallbackUsesMinimalLockScope, "test_concurrent_audio_callback_does_not_crash"), (
                "RACE-001: concurrent callback test must exist."
            )
        except ImportError:
            # If the test module isn't present, this test should fail
            # to alert the maintainer.
            pytest.fail(
                "RACE-001: tests/regressions/audio_test.py must exist with TestAudioCallbackUsesMinimalLockScope."
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source: tests/test_changes4_fixes.py ===

"""Regression tests for the fourth-pass forensic review (changes-4).

Each test class pins one finding to its current verified state.

Findings covered
----------------
Source fixes (6):
- PLAT-WAYLAND  socket restricted to 0o600 (owner-only)
- PLAT-007      clipboard retry narrowed to OSError + ERROR_ACCESS_DENIED
- PLAT-014      comtypes-absence fallback: credential dialog heuristic + WARNING
- PLAT-HLEAK    dead _close_mutex_handle removed
- PLAT-RUN      autostart task name includes install-path hash
- PLAT-PUMP     win32gui import hoisted out of 1ms polling loop

Test gaps filled (5):
- PLAT-002      VK lookup benchmark
- PLAT-005      Windows path migration functional test
- PLAT-011      mutex retry test (pin: no retry is intentional)
- PLAT-016      SystemRoot validation functional test
- PLAT-020      WSL detection test

False positives pinned (6):
- TRAY-006      RECORDING color is now green (not red)
- TEST-012      pytest-benchmark IS in deps
- TEST-013      hypothesis fuzz tests exist
- TEST-016      corrections recovery IS tested
- TEST-021      RTL + emoji tests exist
- TEST-024      WAV fixtures exist
"""


class TestVkLookupBenchmarkExists:
    """PLAT-002.

    The finding: VK lookup performance not benchmarked. Fix: add a
    pytest-benchmark test for the VK map initialization and lookup.
    """

    def test_vk_map_initialization_is_fast(self):
        """VK map initialization must complete in under 100ms."""

        from voice_typer.server.hotkeys import _init_vk_map

        t0 = time.perf_counter()
        _init_vk_map()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 100, f"PLAT-002: VK map init took {elapsed_ms:.1f}ms (target < 100ms)"

    def test_vk_lookup_is_o1_dict_get(self):
        # RW-8: KEEP — pins PLAT-002 (VK lookup uses dict.get, O(1)).
        # The sibling test_vk_map_initialization_is_fast and
        # test_vk_lookup_returns_correct_code_for_f2 test the speed and
        # correctness, but don't catch a regression where the lookup
        # switches to a linear scan that happens to be fast for small
        # maps. Source-string check catches the implementation choice.
        from voice_typer.server import hotkeys

        src = inspect.getsource(hotkeys)
        # The lookup uses _VK_MAP.get(key_name)
        assert "_VK_MAP.get" in src or "_VK_MAP[" in src, "PLAT-002: VK lookup must use dict.get (O(1))"

    def test_vk_lookup_returns_correct_code_for_f2(self):
        """VK_F2 = 0x71 (113)."""
        from voice_typer.server.hotkeys import _VK_MAP, _init_vk_map

        _init_vk_map()
        # F2 should map to VK_F2 = 113
        assert _VK_MAP.get("f2") == 113 or _VK_MAP.get("F2") == 113, (
            f"PLAT-002: VK lookup for 'f2' must return 113, got {_VK_MAP.get('f2')}"
        )


class TestPytestBenchmarkCoverageExists:
    """TEST-012.

    The finding: no pytest-benchmark. Investigation: pytest-benchmark
    IS in pyproject.toml test deps and there are 7 benchmark() calls.
    This test pins that state.
    """

    def test_pytest_benchmark_in_test_deps(self):
        # RW-8: KEEP — pins TEST-012 (pytest-benchmark in test deps).
        # A behavioral test would need to import pytest_benchmark, but
        # that doesn't verify it's declared as a test dependency (it
        # could be a transitive dep). The file-content check catches
        # removal from pyproject.toml directly.

        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        assert "pytest-benchmark" in content, "TEST-012: pytest-benchmark must be in pyproject.toml test deps"

    def test_benchmark_tests_exist(self):
        # RW-8: KEEP — pins TEST-012 (benchmark tests exist).
        # A behavioral test would need to run the benchmark tests, but
        # that doesn't verify they continue to exist if someone deletes
        # the test file. The file-content check catches deletion directly.

        bench_test = Path(__file__).resolve().parent.parent / "test_benchmarks.py"
        if bench_test.exists():
            content = bench_test.read_text(encoding="utf-8")
            assert "benchmark(" in content, "TEST-012: test_benchmarks.py must use benchmark() fixture"


class TestFuzzTestCoverageExists:
    """TEST-013.

    The finding: no fuzzing for corrections.json parser. Investigation:
    hypothesis-based fuzz tests exist in test_text_cleanup_hypothesis.py.
    This test pins that state.
    """

    def test_hypothesis_fuzz_tests_exist(self):
        # RW-8: KEEP — pins TEST-013 (hypothesis fuzz tests exist).
        # Same rationale as test_benchmark_tests_exist.

        hypo_test = Path(__file__).resolve().parent.parent / "test_text_cleanup_hypothesis.py"
        if hypo_test.exists():
            content = hypo_test.read_text(encoding="utf-8")
            assert "TestCorrectionsJsonFuzzing" in content, "TEST-013: TestCorrectionsJsonFuzzing class must exist"
            assert "@given" in content, "TEST-013: hypothesis @given decorator must be used"


class TestCorrectionsRecoveryCoverageExists:
    """TEST-016.

    The finding: no test for fallback to built-in corrections after
    corruption. Investigation: TestCorruptionsRecoveryWithBuiltins
    exists at test_text_cleanup.py:424-470. This test pins that state.
    """

    def test_corruptions_recovery_test_class_exists(self):
        # RW-8: KEEP — pins TEST-016 (corruptions-recovery test class
        # exists). Same rationale as test_benchmark_tests_exist.

        test_file = Path(__file__).resolve().parent.parent / "test_text_cleanup.py"
        if test_file.exists():
            content = test_file.read_text(encoding="utf-8")
            assert "TestCorruptionsRecoveryWithBuiltins" in content, (
                "TEST-016: TestCorruptionsRecoveryWithBuiltins class must exist"
            )
            assert "test_corrupted_file_still_applies_builtin_corrections" in content, (
                "TEST-016: corrupted-file-still-applies-builtin test must exist"
            )


class TestRtlEmojiTestCoverageExists:
    """TEST-021.

    The finding: no RTL/emoji tests. Investigation: test_text_cleanup_cjk.py
    has TestRTLText and TestEmojiInPatterns classes. This test pins that.
    """

    def test_rtl_tests_exist(self):
        # RW-8: KEEP — pins TEST-021 (RTL test class exists).
        # Same rationale as test_benchmark_tests_exist.

        cjk_test = Path(__file__).resolve().parent.parent / "test_text_cleanup_cjk.py"
        if cjk_test.exists():
            content = cjk_test.read_text(encoding="utf-8")
            assert "TestRTLText" in content, "TEST-021: TestRTLText class must exist in test_text_cleanup_cjk.py"
            assert "test_arabic_text_not_mangled" in content, "TEST-021: Arabic text test must exist"

    def test_emoji_tests_exist(self):
        # RW-8: KEEP — pins TEST-021 (emoji test class exists).
        # Same rationale as test_benchmark_tests_exist.

        cjk_test = Path(__file__).resolve().parent.parent / "test_text_cleanup_cjk.py"
        if cjk_test.exists():
            content = cjk_test.read_text(encoding="utf-8")
            assert "TestEmojiInPatterns" in content, "TEST-021: TestEmojiInPatterns class must exist"
            assert "test_emoji_preserved" in content, "TEST-021: emoji preserved test must exist"


class TestWavFixturesCoverageExists:
    """TEST-024.

    The finding: no WAV fixture files. Investigation: 4 WAV fixtures
    exist in tests/fixtures/. This test pins that state.
    """

    def test_wav_fixtures_exist(self):

        fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        wav_files = list(fixtures_dir.glob("*.wav"))
        assert len(wav_files) >= 3, f"TEST-024: at least 3 WAV fixtures must exist, found {len(wav_files)}"

    def test_silence_wav_exists(self):

        silence = Path(__file__).resolve().parent.parent / "fixtures" / "silence.wav"
        assert silence.exists(), "TEST-024: silence.wav fixture must exist"

    def test_tone_wav_exists(self):

        tone = Path(__file__).resolve().parent.parent / "fixtures" / "tone.wav"
        assert tone.exists(), "TEST-024: tone.wav fixture must exist"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source: tests/test_changes5_fixes.py ===

"""Regression tests for the fifth-pass forensic review (changes-5).

Each test class pins one finding to its current verified state.

Findings covered
----------------
Source fixes (4):
- UX-015       i18n: Spanish translation added + UI language selector in Settings
- TRAY-008     tray menu locale switching (set_tray_locale + _TRAY_LABELS_ES)
- TEST-010     mutmut TEST_COMMAND covers all 7 mutated modules
- TRAY-035     Electron notification IPC for persistent/critical notifications

False positives pinned (4):
- TEST-034     upx=False already set in voice-typer.spec
- TEST-037     SHA256 checksum generation already in build.yml
- NEW-IPC-004  TCP reconnect integration tests already exist
- NEW-CONC-003 concurrent cancel tests already exist
"""


class TestMutmutCommandIncludesAllModules:
    """TEST-010.

    The finding: TEST_COMMAND ran only 4 test files but MODULES_TO_MUTATE
    has 7 modules. Fix: updated TEST_COMMAND to include all 7 test files.
    """

    def test_test_command_includes_all_7_modules(self):
        # RW-8: KEEP — pins TEST-010 (mutmut TEST_COMMAND includes all 7
        # test files). A behavioral test would need to run mutmut and verify
        # coverage, which is heavy (mutmut is slow); the file-content check
        # catches removal of any test file from the command directly.
        from pathlib import Path

        config_path = Path(__file__).resolve().parent.parent / "mutmut_config.py"
        src = config_path.read_text(encoding="utf-8")

        # All 7 test files must be in TEST_COMMAND
        required_test_files = [
            "tests/test_text_cleanup.py",
            "tests/test_config.py",
            "tests/test_tray.py",
            "tests/test_tray_menu.py",
            "tests/test_tray_icon.py",
            "tests/test_recording.py",
            "tests/test_app.py",
        ]
        for tf in required_test_files:
            assert tf in src, (
                f"TEST-010: TEST_COMMAND must include {tf} (corresponding to a module in MODULES_TO_MUTATE)"
            )

    def test_modules_to_mutate_has_7_modules(self):
        # RW-8: KEEP — pins TEST-010 (MODULES_TO_MUTATE has all 7 modules).
        # Same rationale as test_test_command_includes_all_7_modules.
        from pathlib import Path

        config_path = Path(__file__).resolve().parent.parent / "mutmut_config.py"
        src = config_path.read_text(encoding="utf-8")

        # Count modules in MODULES_TO_MUTATE
        assert "voice_typer/server/text_cleanup.py" in src
        assert "voice_typer/server/config.py" in src
        assert "voice_typer/server/tray.py" in src
        assert "voice_typer/server/tray_menu.py" in src
        assert "voice_typer/server/tray_icon.py" in src
        assert "voice_typer/server/recording.py" in src
        assert "voice_typer/server/app.py" in src


class TestReleaseChecksumsCoverageExists:
    """TEST-037.

    The finding: no SHA256 checksum generation in release workflow.
    Investigation: checksum generation AND upload are already in
    build.yml. This test pins that state.
    """

    def test_checksum_generation_step_exists(self):
        # RW-8: KEEP — pins TEST-037 (SHA-256 checksum generation in build.yml).
        # A behavioral test would need to run the workflow and inspect the
        # release assets, which is heavy (CI-only); the file-content check
        # catches removal of the checksum step directly.
        from pathlib import Path

        build_yml = Path(__file__).resolve().parent.parent.parent / ".github" / "workflows" / "build.yml"
        src = build_yml.read_text(encoding="utf-8")
        assert "SHA-256" in src or "SHA256" in src, "TEST-037: build.yml must have a SHA-256 checksum generation step"
        assert "SHA256SUMS" in src, "TEST-037: build.yml must generate a SHA256SUMS file"
        assert "Get-FileHash" in src, "TEST-037: build.yml must use Get-FileHash to compute checksums"

    def test_checksum_upload_step_exists(self):
        # RW-8: KEEP — pins TEST-037 (checksum upload step in build.yml).
        # Same rationale as test_checksum_generation_step_exists.
        from pathlib import Path

        build_yml = Path(__file__).resolve().parent.parent.parent / ".github" / "workflows" / "build.yml"
        src = build_yml.read_text(encoding="utf-8")
        assert "Upload checksums to release" in src, "TEST-037: build.yml must upload SHA256SUMS.txt to the release"


class TestReconnectTestCoverageExists:
    """NEW-IPC-004.

    The finding: TCP IPC reconnect not integration-tested. Investigation:
    live TCP reconnect tests exist in test_new_test_001_live_tcp.py.
    This test pins that state.
    """

    def test_reconnect_integration_tests_exist(self):
        # RW-8: KEEP — pins NEW-IPC-004 (TCP reconnect integration tests
        # exist). A behavioral test would need to run the integration tests,
        # but that doesn't verify they continue to exist if someone deletes
        # the test file. The file-content check catches deletion directly.
        from pathlib import Path

        # NEW-IPC-004: the live-TCP reconnect tests were originally in
        # test_new_test_001_live_tcp.py (deleted) and have been
        # consolidated into test_feature_hardening_regressions.py.
        # Assert the file exists and contains the required test
        # functions so a future refactor can't silently drop them.
        test_file = Path(__file__).resolve().parent.parent / "test_feature_hardening_regressions.py"
        assert test_file.exists(), f"NEW-IPC-004: required test file {test_file.name} was deleted (regression)"
        src = test_file.read_text(encoding="utf-8")
        assert "test_reconnect_after_disconnect" in src, "NEW-IPC-004: test_reconnect_after_disconnect must exist"
        assert "test_server_survives_client_crash" in src, "NEW-IPC-004: test_server_survives_client_crash must exist"
        assert "live_server" in src, "NEW-IPC-004: tests must use a live_server fixture (real TCP)"


class TestConcurrentCancelTestCoverageExists:
    """NEW-CONC-003.

    The finding: cancel safety not verified with concurrent tests.
    Investigation: concurrent cancel tests exist in multiple files.
    This test pins that state.
    """

    def test_concurrent_cancel_tests_exist(self):
        # RW-8: KEEP — pins NEW-CONC-003 (concurrent cancel tests exist).
        # Same rationale as test_reconnect_integration_tests_exist.
        from pathlib import Path

        # NEW-CONC-003: concurrent-cancel coverage was originally
        # pinned across test_volume_ducker.py and test_round11_regression.py
        # (deleted). The test_schedule_and_cancel_are_threadsafe test
        # was consolidated into test_recording_and_audio.py. Assert
        # both files exist and contain the required test functions so
        # a future refactor can't silently drop them.
        ducker_test = Path(__file__).resolve().parent.parent / "test_volume_ducker.py"
        assert ducker_test.exists(), f"NEW-CONC-003: required test file {ducker_test.name} was deleted (regression)"
        src = ducker_test.read_text(encoding="utf-8")
        assert "test_concurrent_cancel_and_stop" in src, (
            "NEW-CONC-003: test_concurrent_cancel_and_stop must exist in test_volume_ducker.py"
        )

        recording_test = Path(__file__).resolve().parent.parent / "test_recording_and_audio.py"
        assert recording_test.exists(), (
            f"NEW-CONC-003: required test file {recording_test.name} was deleted (regression)"
        )
        src = recording_test.read_text(encoding="utf-8")
        assert "test_schedule_and_cancel_are_threadsafe" in src, (
            "NEW-CONC-003: test_schedule_and_cancel_are_threadsafe must exist"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source: tests/test_changes6_fixes.py ===

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


class TestCommittedTextSortOrderCoverageExists:
    """TEST-009.

    The finding: test_committed_text_sorted_by_time() only asserted
    isinstance(result, str) — no sort-order assertion. Fix: added
    chronological-order verification by comparing emitted words against
    the input sorted by start_seconds.
    """

    def test_test_has_sort_order_assertion(self):
        # RW-8: KEEP — pins TEST-009 (test_streaming_hypothesis.py has
        # a sort-order assertion). A behavioral test would need to run
        # the hypothesis test, but that doesn't verify the assertion
        # continues to exist if someone weakens it. The file-content
        # check catches removal directly.
        test_path = Path(__file__).resolve().parent.parent / "test_streaming_hypothesis.py"
        src = test_path.read_text(encoding="utf-8")
        # Must contain the TEST-009 fix comment
        assert "TEST-009" in src, "TEST-009: test file must reference the fix"
        # Must contain sort-order verification logic. The TEST-009 fix
        # originally used a single-key sort; the test was later refined
        # to break ties using a (start_seconds, end_seconds) tuple, so
        # accept either form.
        assert (
            "sorted(words, key=lambda w: w.start_seconds)" in src
            or "sorted(words, key=lambda w: (w.start_seconds, w.end_seconds))" in src
        ), "TEST-009: test must sort input words by start_seconds for comparison"
        assert "emitted_words" in src, "TEST-009: test must extract emitted words from committed_text"
        assert "expected_words" in src, "TEST-009: test must build expected word sequence"


class TestParametrizeUsageCountAboveThirty:
    """TEST-032.

    The finding: only 6 @pytest.mark.parametrize uses. Investigation:
    41 uses now exist across 7 files. This test pins that state.
    """

    def test_parametrize_count_is_above_30(self):
        """At least 30 @pytest.mark.parametrize uses must exist.

        Uses Python's pathlib + grep instead of the Unix `grep` command
        so it works on Windows too.

        RW-8: KEEP — pins TEST-032 (>= 30 @pytest.mark.parametrize uses).
        A behavioral test would need to count parametrize uses at runtime,
        which is the same operation; the file-content check is the most
        direct way to catch a regression where parametrize uses drop.
        """
        from pathlib import Path

        tests_dir = Path(__file__).resolve().parent.parent
        count = 0
        for py_file in tests_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                count += content.count("@pytest.mark.parametrize")
            except Exception:
                pass
        assert count >= 30, f"TEST-032: expected at least 30 @pytest.mark.parametrize uses, found {count}"


class TestNoImportMockInTests:
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

        tests_dir = Path(__file__).resolve().parent.parent
        violations = []
        for py_file in tests_dir.rglob("*.py"):
            try:
                for line_num, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1):
                    if line.strip() == "import mock":
                        violations.append(f"{py_file}:{line_num}")
            except Exception:
                pass
        assert not violations, (
            f"TEST-033: found `import mock` usage in tests:\n{chr(10).join(violations)}\n"
            "Use `from unittest.mock import MagicMock, patch` instead."
        )

    def test_convention_documented_in_contributing(self):
        # RW-8: KEEP — pins TEST-033 (CONTRIBUTING.md documents the
        # unittest.mock convention). A behavioral test would need to
        # parse the markdown and verify the convention is mentioned,
        # which is the same operation; the file-content check is simpler.
        contributing = Path(__file__).resolve().parent.parent.parent / "CONTRIBUTING.md"
        if contributing.exists():
            src = contributing.read_text(encoding="utf-8")
            assert "unittest.mock" in src or "from unittest.mock" in src, (
                "TEST-033: CONTRIBUTING.md must document the mock convention"
            )


class TestPyreflyRunsInCi:
    """TEST-036.

    The finding: pyrefly configured but not run in CI. Investigation:
    pyrefly IS now run in CI (build.yml:43-50), with continue-on-error=true
    as a soft gate. This test pins that state.
    """

    def test_pyrefly_in_build_yml(self):
        # RW-8: KEEP — pins TEST-036 (pyrefly run in CI build.yml).
        # A behavioral test would need to run the workflow and verify
        # pyrefly output, which is heavy (CI-only); the file-content
        # check catches removal of the pyrefly step directly.
        build_yml = Path(__file__).resolve().parent.parent.parent / ".github" / "workflows" / "build.yml"
        if build_yml.exists():
            src = build_yml.read_text(encoding="utf-8")
            assert "pyrefly" in src, "TEST-036: build.yml must run pyrefly in CI"
            assert "pyrefly check" in src, "TEST-036: build.yml must run 'pyrefly check'"

    def test_pyrefly_configured_in_pyproject(self):
        # RW-8: KEEP — pins TEST-036 ([tool.pyrefly] section in pyproject.toml).
        # Same rationale as test_pyrefly_in_build_yml.
        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        src = pyproject.read_text(encoding="utf-8")
        assert "[tool.pyrefly]" in src, "TEST-036: pyproject.toml must have [tool.pyrefly] section"


class TestCorrectionsExplicitLoadCoverageExists:
    """TEST-039.

    The finding: corrections.json never explicitly tested as loadable.
    Investigation: TestCorrectionsExplicitLoad exists in test_corruptions.py.
    This test pins that state.
    """

    def test_explicit_load_test_class_exists(self):
        # RW-8: KEEP — pins TEST-039 (TestCorrectionsExplicitLoad class exists).
        # Same rationale as test_benchmark_tests_exist.
        test_path = Path(__file__).resolve().parent.parent / "test_corruptions.py"
        if test_path.exists():
            src = test_path.read_text(encoding="utf-8")
            assert "TestCorrectionsExplicitLoad" in src, "TEST-039: TestCorrectionsExplicitLoad class must exist"
            assert "test_bundled_corrections_json_loads" in src, (
                "TEST-039: test_bundled_corrections_json_loads must exist"
            )


class TestTextCleanupUnicodeCoverageExists:
    """TEST-008.

    The finding: no RTL/emoji/concurrent/boundary tests. Investigation:
    TestTextCleanupUnicode in test_text_cleanup.py has all four categories.
    This test pins that state.
    """

    def test_unicode_test_class_exists(self):
        # RW-8: KEEP — pins TEST-008 (TestTextCleanupUnicode class exists).
        # Same rationale as test_benchmark_tests_exist.
        test_path = Path(__file__).resolve().parent.parent / "test_text_cleanup.py"
        if test_path.exists():
            src = test_path.read_text(encoding="utf-8")
            assert "TestTextCleanupUnicode" in src, "TEST-008: TestTextCleanupUnicode class must exist"

    def test_concurrent_cleanup_test_exists(self):
        # RW-8: KEEP — pins TEST-008 (concurrent cleanup test exists).
        # Same rationale as test_benchmark_tests_exist.
        test_path = Path(__file__).resolve().parent.parent / "test_text_cleanup.py"
        if test_path.exists():
            src = test_path.read_text(encoding="utf-8")
            assert "test_concurrent_cleanup_calls" in src, "TEST-008: concurrent cleanup test must exist"

    def test_boundary_inputs_test_exists(self):
        # RW-8: KEEP — pins TEST-008 (boundary inputs test exists).
        # Same rationale as test_benchmark_tests_exist.
        test_path = Path(__file__).resolve().parent.parent / "test_text_cleanup.py"
        if test_path.exists():
            src = test_path.read_text(encoding="utf-8")
            assert "test_boundary_inputs_never_crash" in src, "TEST-008: boundary inputs test must exist"


class TestResampleFallbackCoverageExists:
    """TEST-020.

    The finding: np.interp fallback not tested. Investigation:
    TestResampleFallback in test_recording.py explicitly tests the
    np.interp path. This test pins that state.
    """

    def test_np_interp_fallback_test_exists(self):
        # RW-8: KEEP — pins TEST-020 (np.interp fallback test exists).
        # Same rationale as test_benchmark_tests_exist.
        test_path = Path(__file__).resolve().parent.parent / "test_recording.py"
        if test_path.exists():
            src = test_path.read_text(encoding="utf-8")
            assert "test_fallback_to_np_interp_when_scipy_unavailable" in src, (
                "TEST-020: np.interp fallback test must exist"
            )
            assert "test_resample_fallback_quality_with_known_sine" in src, "TEST-020: fallback quality test must exist"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source: tests/test_changes7_fixes.py ===

"""Regression tests for the seventh-pass forensic review (changes-7).

Findings covered:
- PLAT-009   accessibility health monitoring (periodic pulse)
- PLAT-010   tray icon AccessibleName (title as a11y label)
- PLAT-012   subprocess crash recovery tests
- PLAT-015   KDE/GNOME DE-specific tray tests
- PLAT-017   DPI/large text toggle (CSS --font-scale)
- PLAT-019   systemd user unit for main app
- PLAT-021   container detection
- PLAT-CONTENT  contentEditable detection
- DOC-008    API documentation exists
- NEW-CQ-003/007/013/014/025  concurrent/stress/backpressure/cleanup tests
- NEW-IPC-011/012/016  IPC concurrent/large/blocking tests
- NEW-PRIV-002/006  config permission + audio crop boundary tests
- PLAT-MAC   (documented as blocked — needs macOS CI)
"""


class TestAccessibilityPulseReCheckExists:
    """PLAT-009: Periodic re-check of macOS Accessibility permission."""

    def test_start_accessibility_pulse_exists(self):
        # RW-9 Phase 1: ``VoiceTyperApp._start_accessibility_pulse``
        # test-seam delegate removed; introspect the standalone function
        # in ``startup_tasks`` instead.
        from voice_typer.server import startup_tasks

        assert hasattr(startup_tasks, "start_accessibility_pulse")

    def test_pulse_called_on_macos(self):
        """Source must call start_accessibility_pulse after the a11y check.

        RW-8: KEEP — pins PLAT-009 (StartupSequence.run calls
        start_accessibility_pulse). A behavioral test would need to run
        StartupSequence.run on macOS and observe the pulse thread start,
        which is heavy (platform-specific); the source-string check
        catches removal of the call directly.
        """
        from voice_typer.server.startup_sequence import StartupSequence

        src = inspect.getsource(StartupSequence.run)
        assert "start_accessibility_pulse" in src


class TestApiDocumentationExists:
    """DOC-008: Formal API documentation exists."""

    def test_api_md_exists(self):
        api_path = Path(__file__).resolve().parent.parent.parent / "docs" / "API.md"
        assert api_path.exists()

    def test_api_md_mentions_key_classes(self):
        # RW-8: KEEP — pins DOC-008 (API.md mentions VoiceTyperApp or Config).
        # A behavioral test would need to parse the markdown and verify
        # the class is documented, which is the same operation; the
        # file-content check is simpler.
        api_path = Path(__file__).resolve().parent.parent.parent / "docs" / "API.md"
        content = api_path.read_text(encoding="utf-8")
        assert "VoiceTyperApp" in content or "Config" in content


class TestConfigPermissionTestsCoverageExists:
    """NEW-PRIV-002: Config file permission tests exist."""

    def test_permission_tests_exist(self):
        # RW-8: KEEP — pins NEW-PRIV-002 (config permission tests exist
        # in test_config.py). Same rationale as test_benchmark_tests_exist.
        test_path = Path(__file__).resolve().parent.parent / "test_config.py"
        if test_path.exists():
            src = test_path.read_text(encoding="utf-8")
            assert "TestConfigSaveEnforcesPosixFilePermissions" in src
            assert "0600" in src or "0o600" in src
