"""XV-123: tests for the cached pyobjc availability flag in permissions.py.

The previous code re-attempted ``from AVFoundation import ...`` /
``from ApplicationServices import ...`` / ``from CoreFoundation import
...`` on EVERY call to ``_check_macos_microphone`` /
``_check_macos_accessibility``. When pyobjc was missing (e.g. on Linux
or CI), every call paid the full import-lookup cost. XV-123 caches the
availability at module level so subsequent calls are O(1).

These tests are platform-agnostic: they monkeypatch
``_is_pyobjc_available`` to simulate the "pyobjc missing" /
"pyobjc available" branches without actually requiring macOS frameworks.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import permissions
from voice_typer.server.permissions import (
    MicrophonePermissionState,
    PermissionState,
    _check_macos_accessibility,
    _check_macos_microphone,
    _is_pyobjc_available,
    reset_pyobjc_cache,
)


@pytest.fixture(autouse=True)
def _reset_pyobjc_cache_between_tests():
    """Each test starts with a clean cache so the cache state from a
    previous test doesn't leak in."""
    reset_pyobjc_cache()
    yield
    reset_pyobjc_cache()


class TestPyobjcCacheBasics:
    """XV-123: ``_is_pyobjc_available`` caches its result across calls."""

    def test_initial_cache_is_none(self):
        """Before any probe, the cache is unset (``None``)."""
        assert permissions._PYOBJC_AVAILABLE is None

    def test_probe_sets_cache(self):
        """The first probe sets the cache to a concrete bool."""
        result = _is_pyobjc_available()
        assert isinstance(result, bool)
        assert result == permissions._PYOBJC_AVAILABLE

    def test_subsequent_probes_return_cached_value(self):
        """Calling ``_is_pyobjc_available`` twice returns the same value
        without re-probing."""
        first = _is_pyobjc_available()
        second = _is_pyobjc_available()
        assert first == second
        assert first == permissions._PYOBJC_AVAILABLE

    def test_reset_clears_cache(self):
        """``reset_pyobjc_cache`` resets the cache to ``None``."""
        _is_pyobjc_available()
        assert permissions._PYOBJC_AVAILABLE is not None
        reset_pyobjc_cache()
        assert permissions._PYOBJC_AVAILABLE is None

    def test_cached_probes_do_not_re_import(self):
        """Once cached, subsequent probes don't re-attempt the import.

        Verified by patching ``builtins.__import__`` to count
        ApplicationServices import attempts — only the FIRST probe should
        trigger the import.
        """
        original_import = __import__
        call_count = {"n": 0}

        def counting_import(name, *args, **kwargs):
            if name == "ApplicationServices":
                call_count["n"] += 1
                raise ImportError(f"simulated missing {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=counting_import):
            _is_pyobjc_available()  # first probe — should attempt import
            assert call_count["n"] == 1
            _is_pyobjc_available()  # cached — should NOT attempt import
            _is_pyobjc_available()  # cached — should NOT attempt import
            _is_pyobjc_available()  # cached — should NOT attempt import
            assert call_count["n"] == 1, (
                f"cached probes must not re-import; got {call_count['n']} attempts"
            )


class TestPyobjcCacheMissingPath:
    """When pyobjc is missing, the macOS probes short-circuit to UNKNOWN."""

    def test_microphone_returns_unknown_when_pyobjc_missing(self):
        """``_check_macos_microphone`` returns UNKNOWN without attempting
        the AVFoundation import when the cache says pyobjc is missing."""
        # Force the cache to "missing".
        with patch.object(permissions, "_PYOBJC_AVAILABLE", False):
            # Even if AVFoundation were importable, we shouldn't attempt
            # the import. Patch the builtins to fail loudly if it does.
            original_import = __import__

            def fail_avfoundation(name, *args, **kwargs):
                if name == "AVFoundation":
                    raise AssertionError(
                        "AVFoundation import attempted despite pyobjc cached as missing"
                    )
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fail_avfoundation):
                result = _check_macos_microphone()
            assert result == MicrophonePermissionState.UNKNOWN

    def test_accessibility_returns_unknown_when_pyobjc_missing(self):
        """``_check_macos_accessibility`` returns UNKNOWN without attempting
        the ApplicationServices / CoreFoundation imports when the cache
        says pyobjc is missing."""
        with patch.object(permissions, "_PYOBJC_AVAILABLE", False):
            original_import = __import__

            def fail_appservices(name, *args, **kwargs):
                if name in ("ApplicationServices", "CoreFoundation"):
                    raise AssertionError(
                        f"{name} import attempted despite pyobjc cached as missing"
                    )
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fail_appservices):
                result = _check_macos_accessibility()
            assert result == PermissionState.UNKNOWN


class TestPyobjcCacheAvailablePath:
    """When pyobjc is available, the macOS probes attempt the imports
    (and the per-probe ImportError falls back to UNKNOWN + cache update)."""

    def test_microphone_attempts_avfoundation_when_cached_available(self):
        """``_check_macos_microphone`` attempts the AVFoundation import
        when the cache says pyobjc is available. If AVFoundation itself
        is missing (partial pyobjc install), the cache is updated to
        ``False`` so future probes short-circuit."""
        with patch.object(permissions, "_PYOBJC_AVAILABLE", True):
            # Simulate AVFoundation missing despite cache saying available.
            with patch("builtins.__import__", side_effect=__import__):
                # Actually: we need to make the AVFoundation import fail.
                # Use a direct module patch.
                import sys
                original = sys.modules.get("AVFoundation")
                sys.modules["AVFoundation"] = None  # forces ImportError
                try:
                    result = _check_macos_microphone()
                finally:
                    if original is None:
                        sys.modules.pop("AVFoundation", None)
                    else:
                        sys.modules["AVFoundation"] = original
            assert result == MicrophonePermissionState.UNKNOWN
            # XV-123 invariant: cache is updated to False so future
            # calls short-circuit.
            assert permissions._PYOBJC_AVAILABLE is False

    def test_accessibility_attempts_imports_when_cached_available(self):
        """``_check_macos_accessibility`` attempts the ApplicationServices
        and CoreFoundation imports when the cache says pyobjc is
        available. If they're missing (partial install), the cache is
        updated to ``False``."""
        with patch.object(permissions, "_PYOBJC_AVAILABLE", True):
            import sys
            # Force ImportError for both modules.
            for mod in ("ApplicationServices", "CoreFoundation"):
                sys.modules[mod] = None
            try:
                result = _check_macos_accessibility()
            finally:
                for mod in ("ApplicationServices", "CoreFoundation"):
                    sys.modules.pop(mod, None)
            assert result == PermissionState.UNKNOWN
            assert permissions._PYOBJC_AVAILABLE is False

    def test_accessibility_returns_granted_when_pyobjc_works(self):
        """End-to-end: with pyobjc available and the macOS APIs mocked,
        ``_check_macos_accessibility`` returns GRANTED."""
        with patch.object(permissions, "_PYOBJC_AVAILABLE", True):
            # Mock the ApplicationServices and CoreFoundation modules.
            mock_appservices = MagicMock()
            mock_appservices.AXIsProcessTrustedWithOptions.return_value = True
            mock_corefoundation = MagicMock()
            mock_corefoundation.CFDictionaryCreate.return_value = MagicMock()

            import sys
            original_appservices = sys.modules.get("ApplicationServices")
            original_corefoundation = sys.modules.get("CoreFoundation")
            sys.modules["ApplicationServices"] = mock_appservices
            sys.modules["CoreFoundation"] = mock_corefoundation
            try:
                result = _check_macos_accessibility()
            finally:
                if original_appservices is None:
                    sys.modules.pop("ApplicationServices", None)
                else:
                    sys.modules["ApplicationServices"] = original_appservices
                if original_corefoundation is None:
                    sys.modules.pop("CoreFoundation", None)
                else:
                    sys.modules["CoreFoundation"] = original_corefoundation

            assert result == PermissionState.GRANTED
            mock_appservices.AXIsProcessTrustedWithOptions.assert_called_once()

    def test_microphone_returns_granted_when_pyobjc_works(self):
        """End-to-end: with pyobjc available and AVFoundation mocked,
        ``_check_macos_microphone`` returns GRANTED."""
        with patch.object(permissions, "_PYOBJC_AVAILABLE", True):
            mock_avfoundation = MagicMock()
            mock_avfoundation.AVCaptureDevice.authorizationStatusForMediaType_.return_value = 2  # Authorized
            mock_avfoundation.AVMediaTypeAudio.return_value = "audio"

            import sys
            original = sys.modules.get("AVFoundation")
            sys.modules["AVFoundation"] = mock_avfoundation
            try:
                result = _check_macos_microphone()
            finally:
                if original is None:
                    sys.modules.pop("AVFoundation", None)
                else:
                    sys.modules["AVFoundation"] = original

            assert result == MicrophonePermissionState.GRANTED


class TestPyobjcCachePerformance:
    """XV-123: cached probes are dramatically faster than re-importing."""

    def test_cached_probes_are_o1(self):
        """1000 cached probes should complete in well under 10ms (the
        cost of a single import attempt when pyobjc is missing is
        typically 0.1-1ms; 1000 such imports would be 100-1000ms)."""
        import time

        # Prime the cache.
        _is_pyobjc_available()

        # Time 1000 cached probes.
        t0 = time.perf_counter()
        for _ in range(1000):
            _is_pyobjc_available()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # 10ms is generous — typical cached probes are <0.1ms total
        # for 1000 calls. The point is to verify the cache is consulted
        # (not that we're at any particular speed).
        assert elapsed_ms < 10.0, (
            f"1000 cached probes took {elapsed_ms:.2f}ms — cache not consulted?"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
