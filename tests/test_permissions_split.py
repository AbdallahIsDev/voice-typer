"""Tests for the ``permissions`` package split.

The original 1144-LOC ``permissions.py`` monolith was split into a
package with 4 focused submodules + a facade ``__init__.py``.
These tests verify:

1. **Backward compatibility** — every name that tests previously
   imported from ``voice_typer.server.permissions`` is still
   accessible on the facade. This includes:
     - Public functions: ``check_keyboard_permission``,
       ``check_microphone_permission``, ``verify_microphone_accessible``,
       ``request_keyboard_permission``,
       ``request_microphone_permission``,
       ``request_microphone_permission_result``,
       ``schedule_permission_retry``, ``cancel_permission_retry``,
       ``permission_error_is_permission_denied``,
       ``show_permission_notification``,
       ``_is_pyobjc_available``, ``reset_pyobjc_cache``.
     - Enums: ``PermissionState``, ``MicrophonePermissionState``.
     - Constants: ``LINUX_UDEV_RULE``,
       ``PERMISSION_RETRY_INTERVAL_SECONDS``,
       ``PERMISSION_RETRY_MAX_ATTEMPTS``.
     - Module-level mutable state (read + writable):
       ``_retry_timer``, ``_retry_count``, ``_cancelled``,
       ``_retry_lock``, ``_PYOBJC_AVAILABLE``.
     - Platform check helpers (re-exported from platform_utils so
       tests can monkeypatch a single namespace):
       ``is_windows``, ``is_macos``, ``is_linux``.
     - Stdlib module refs that tests patch via
       ``monkeypatch.setattr(permissions.<mod>, <attr>, <value>)``:
       ``subprocess``, ``os``, ``shutil``, ``threading``.

Note: this file deliberately omits task-ID / session-prefix tags
from source code per C-STYLE-1 (the corresponding review entries
live only in metadata files like ``review.md`` / ``worklog.md``).

2. **State proxying** — test mutations on
   ``permissions._PYOBJC_AVAILABLE = True`` (etc.) are observable by
   the submodule functions that read/write the same state. This is the
   key invariant that lets the existing test suite (which resets
   module-level globals between tests) work without modification.

3. **Function-patch propagation** — monkeypatches on
   ``permissions.<function>`` (e.g. ``check_keyboard_permission``,
   ``_open_macos_accessibility_settings``, ``schedule_permission_retry``)
   are observed by other submodule functions that call them. This is
   the key invariant that lets the existing test suite (which patches
   functions on the facade to isolate behaviors) work without
   modification.

4. **Package structure** — the package directory contains the 4
   submodules specified in the split plan
   (``checker``, ``mic``, ``accessibility``, ``filesystem``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── 1. Backward compatibility: all previously-importable names ───────


# Names that ``tests/test_permissions.py`` and
# ``tests/test_permissions_group_fixes.py`` read or call on the
# ``permissions`` module. Compiled from:
#   grep -rn "permissions\." tests/test_permissions*.py
PUBLIC_FUNCTIONS = [
    "check_keyboard_permission",
    "check_microphone_permission",
    "verify_microphone_accessible",
    "request_keyboard_permission",
    "request_microphone_permission",
    "request_microphone_permission_result",
    "schedule_permission_retry",
    "cancel_permission_retry",
    "permission_error_is_permission_denied",
    "show_permission_notification",
    "reset_pyobjc_cache",
]
PRIVATE_FUNCTIONS = [
    "_is_pyobjc_available",
    "_check_macos_accessibility",
    "_check_macos_microphone",
    "_check_windows_microphone",
    "_check_linux_microphone",
    "_check_linux_input_access",
    "_open_macos_accessibility_settings",
    "_open_macos_microphone_settings",
    "_trigger_macos_microphone_consent_prompt",
    "_open_linux_pkexec_prompt",
    "_find_linux_install_script",
]
ENUMS = [
    "PermissionState",
    "MicrophonePermissionState",
]
CONSTANTS = [
    "LINUX_UDEV_RULE",
    "PERMISSION_RETRY_INTERVAL_SECONDS",
    "PERMISSION_RETRY_MAX_ATTEMPTS",
    "APP_NAME",
    "WHISPER_SAMPLE_RATE",
]
MUTABLE_STATE = [
    "_retry_timer",
    "_retry_count",
    "_cancelled",
    "_retry_lock",
    "_PYOBJC_AVAILABLE",
]
PLATFORM_HELPERS = [
    "is_windows",
    "is_macos",
    "is_linux",
]
STDLIB_MODULE_REFS = [
    "subprocess",
    "os",
    "shutil",
    "threading",
    "sys",
    "contextlib",
    "logging",
]


class TestBackwardCompatNames:
    """Every name that tests previously imported is still on the facade."""

    def test_public_functions_importable(self):
        from voice_typer.server import permissions

        for name in PUBLIC_FUNCTIONS:
            assert hasattr(permissions, name), (
                f"permissions.{name} is missing — public function not re-exported by facade"
            )
            assert callable(getattr(permissions, name)), f"permissions.{name} is not callable"

    def test_private_functions_importable(self):
        from voice_typer.server import permissions

        for name in PRIVATE_FUNCTIONS:
            assert hasattr(permissions, name), (
                f"permissions.{name} is missing — private function not re-exported by facade"
            )
            assert callable(getattr(permissions, name)), f"permissions.{name} is not callable"

    def test_enums_importable(self):
        from voice_typer.server import permissions

        for name in ENUMS:
            assert hasattr(permissions, name), f"permissions.{name} is missing — enum not re-exported by facade"

    def test_constants_importable(self):
        from voice_typer.server import permissions

        for name in CONSTANTS:
            assert hasattr(permissions, name), f"permissions.{name} is missing — constant not re-exported by facade"

    def test_constants_have_correct_values(self):
        """The constants must match the original values exactly."""
        from voice_typer.server import permissions

        assert permissions.LINUX_UDEV_RULE == ('KERNEL=="event[0-9]*", SUBSYSTEM=="input", GROUP="input", MODE="0660"')
        assert permissions.PERMISSION_RETRY_INTERVAL_SECONDS == 60.0
        assert permissions.PERMISSION_RETRY_MAX_ATTEMPTS == 5

    def test_mutable_state_present(self):
        """Mutable state vars must be present on the facade (so tests can
        read/write them and have the writes propagate to submodule
        functions)."""
        from voice_typer.server import permissions

        for name in MUTABLE_STATE:
            assert hasattr(permissions, name), f"permissions.{name} is missing — mutable state not declared on facade"

    def test_mutable_state_writable(self):
        """Test mutations on facade state must propagate (the key
        invariant of the split — mirrors the crash_handler split's
        TestStateProxying test)."""
        from voice_typer.server import permissions

        # Save original values.
        saved = {k: getattr(permissions, k) for k in MUTABLE_STATE}
        try:
            # Write test values.
            permissions._retry_timer = "fake_timer"
            permissions._retry_count = 42
            permissions._cancelled = True
            permissions._PYOBJC_AVAILABLE = True

            # Verify reads see the written values.
            assert permissions._retry_timer == "fake_timer"
            assert permissions._retry_count == 42
            assert permissions._cancelled is True
            assert permissions._PYOBJC_AVAILABLE is True
        finally:
            # Restore.
            for k, v in saved.items():
                setattr(permissions, k, v)

    def test_platform_helpers_importable(self):
        """``is_windows`` / ``is_macos`` / ``is_linux`` must be re-exported
        on the facade so tests can monkeypatch a single namespace
        (mirrors the onboarding.py comment about re-exporting platform
        helpers from permissions)."""
        from voice_typer.server import permissions

        for name in PLATFORM_HELPERS:
            assert hasattr(permissions, name), (
                f"permissions.{name} is missing — platform helper not re-exported by facade"
            )
            assert callable(getattr(permissions, name)), f"permissions.{name} is not callable"

    def test_stdlib_module_refs_present(self):
        """Stdlib module references (``subprocess``, ``os``, ``shutil``,
        ``threading``) must be present as facade attributes so tests can
        do ``monkeypatch.setattr(permissions.subprocess, 'Popen', ...)``."""
        from voice_typer.server import permissions

        for name in STDLIB_MODULE_REFS:
            assert hasattr(permissions, name), f"permissions.{name} is missing — stdlib module ref not on facade"


# ── 2. State proxying: test mutations propagate to submodule functions ──


class TestStateProxying:
    """Test mutations on ``permissions.<state>`` must be observable by
    the submodule functions that read/write the same state.

    This is the key invariant that lets the existing test suite (which
    resets module-level globals between tests via
    ``permissions._PYOBJC_AVAILABLE = None`` etc.) work without
    modification after the split.
    """

    def test_reset_pyobjc_cache_clears_facade_state(self):
        """``reset_pyobjc_cache`` (defined in ``checker``) writes to
        ``_p._PYOBJC_AVAILABLE = None`` — reads on
        ``permissions._PYOBJC_AVAILABLE`` must see the new value."""
        from voice_typer.server import permissions

        # Prime the cache.
        permissions._PYOBJC_AVAILABLE = True
        # The function (in checker) writes to _p._PYOBJC_AVAILABLE —
        # reads on the facade must see the new value.
        permissions.reset_pyobjc_cache()
        assert permissions._PYOBJC_AVAILABLE is None

    def test_facade_reset_visible_to_submodule_function(self):
        """When a test sets ``permissions._PYOBJC_AVAILABLE = False``,
        the next ``_is_pyobjc_available`` call must return False
        WITHOUT re-probing (i.e. it must observe the facade-level
        cache value)."""
        from voice_typer.server import permissions

        saved = permissions._PYOBJC_AVAILABLE
        try:
            # Simulate a test that sets the cache.
            permissions._PYOBJC_AVAILABLE = False
            # The function must observe the facade-level cache value
            # (via _p._PYOBJC_AVAILABLE) and return False without
            # re-probing (which would re-attempt the ApplicationServices
            # import).
            result = permissions._is_pyobjc_available()
            assert result is False, (
                "_is_pyobjc_available did not observe the facade-level "
                "_PYOBJC_AVAILABLE=False — state proxying is broken."
            )
            # The cache must still be False (no re-probe happened).
            assert permissions._PYOBJC_AVAILABLE is False
        finally:
            permissions._PYOBJC_AVAILABLE = saved

    def test_check_macos_accessibility_short_circuits_on_cached_false(self):
        """When ``permissions._PYOBJC_AVAILABLE = False`` is set on the
        facade, ``_check_macos_accessibility`` (defined in
        ``accessibility``) must short-circuit to UNKNOWN without
        attempting the pyobjc imports."""
        from voice_typer.server import permissions

        saved = permissions._PYOBJC_AVAILABLE
        try:
            # Force the cache to "missing".
            permissions._PYOBJC_AVAILABLE = False

            # Even if ApplicationServices were importable, we shouldn't
            # attempt the import. Patch the builtins to fail loudly if it does.
            original_import = __import__

            def fail_appservices(name, *args, **kwargs):
                if name in ("ApplicationServices", "CoreFoundation"):
                    raise AssertionError(f"{name} import attempted despite pyobjc cached as missing")
                return original_import(name, *args, **kwargs)

            from unittest.mock import patch

            with patch("builtins.__import__", side_effect=fail_appservices):
                result = permissions._check_macos_accessibility()
            assert result == permissions.PermissionState.UNKNOWN
        finally:
            permissions._PYOBJC_AVAILABLE = saved

    def test_check_macos_microphone_short_circuits_on_cached_false(self):
        """When ``permissions._PYOBJC_AVAILABLE = False`` is set on the
        facade, ``_check_macos_microphone`` (defined in ``mic``) must
        short-circuit to UNKNOWN without attempting the AVFoundation
        import."""
        from voice_typer.server import permissions

        saved = permissions._PYOBJC_AVAILABLE
        try:
            permissions._PYOBJC_AVAILABLE = False

            original_import = __import__

            def fail_avfoundation(name, *args, **kwargs):
                if name == "AVFoundation":
                    raise AssertionError("AVFoundation import attempted despite pyobjc cached as missing")
                return original_import(name, *args, **kwargs)

            from unittest.mock import patch

            with patch("builtins.__import__", side_effect=fail_avfoundation):
                result = permissions._check_macos_microphone()
            assert result == permissions.MicrophonePermissionState.UNKNOWN
        finally:
            permissions._PYOBJC_AVAILABLE = saved


# ── 3. Function-patch propagation ──────────────────────────────────────


class TestFunctionPatchPropagation:
    """Monkeypatches on ``permissions.<function>`` must be observed by
    other submodule functions that call them. This is the key invariant
    that lets the existing test suite (which patches functions on the
    facade to isolate behaviors) work without modification."""

    def test_check_keyboard_permission_uses_patched_is_macos(self, monkeypatch):
        """``check_keyboard_permission`` (in checker) calls
        ``_p.is_macos()`` — a monkeypatch on ``permissions.is_macos``
        must be observed."""
        from unittest.mock import patch

        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: False)
        monkeypatch.setattr(permissions, "is_macos", lambda: True)
        monkeypatch.setattr(permissions, "is_linux", lambda: False)
        # _check_macos_accessibility will ImportError on pyobjc → UNKNOWN
        with patch.dict(sys.modules, {"CoreFoundation": None, "ApplicationServices": None}):
            result = permissions.check_keyboard_permission()
        assert result == permissions.PermissionState.UNKNOWN

    def test_check_keyboard_permission_dispatches_to_patched_probe(self, monkeypatch):
        """``check_keyboard_permission`` (in checker) calls
        ``_p._check_macos_accessibility()`` — a monkeypatch on
        ``permissions._check_macos_accessibility`` must be observed."""
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: False)
        monkeypatch.setattr(permissions, "is_macos", lambda: True)
        monkeypatch.setattr(permissions, "is_linux", lambda: False)
        # Patch the macOS probe to return DENIED.
        monkeypatch.setattr(
            permissions,
            "_check_macos_accessibility",
            lambda: permissions.PermissionState.DENIED,
        )
        result = permissions.check_keyboard_permission()
        assert result == permissions.PermissionState.DENIED

    def test_request_keyboard_permission_calls_patched_open_settings(self, monkeypatch):
        """``request_keyboard_permission`` (in checker) calls
        ``_p._open_macos_accessibility_settings()`` — a monkeypatch on
        ``permissions._open_macos_accessibility_settings`` must be
        observed."""
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: False)
        monkeypatch.setattr(permissions, "is_macos", lambda: True)
        monkeypatch.setattr(permissions, "is_linux", lambda: False)

        called = []
        monkeypatch.setattr(
            permissions,
            "_open_macos_accessibility_settings",
            lambda: called.append("opened"),
        )
        monkeypatch.setattr(permissions, "schedule_permission_retry", lambda cb, **kw: None)

        permissions.request_keyboard_permission()
        assert called == ["opened"]

    def test_request_keyboard_permission_calls_patched_schedule_retry(self, monkeypatch):
        """``request_keyboard_permission`` (in checker) calls
        ``_p.schedule_permission_retry()`` — a monkeypatch on
        ``permissions.schedule_permission_retry`` must be observed."""
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: False)
        monkeypatch.setattr(permissions, "is_macos", lambda: True)
        monkeypatch.setattr(permissions, "is_linux", lambda: False)
        monkeypatch.setattr(permissions, "_open_macos_accessibility_settings", lambda: None)

        scheduled = []
        monkeypatch.setattr(
            permissions,
            "schedule_permission_retry",
            lambda cb, **kw: scheduled.append(cb),
        )

        cb = lambda: None  # noqa: E731
        permissions.request_keyboard_permission(on_granted=cb)
        assert scheduled == [cb]

    def test_schedule_permission_retry_calls_patched_check(self, monkeypatch):
        """``schedule_permission_retry`` (in checker) calls
        ``_p.check_keyboard_permission()`` from within its ``_poll``
        callback — a monkeypatch on ``permissions.check_keyboard_permission``
        must be observed."""
        import time
        from unittest.mock import MagicMock

        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "check_keyboard_permission",
            lambda: permissions.PermissionState.GRANTED,
        )
        cb = MagicMock()
        permissions.schedule_permission_retry(cb, interval=0.01, max_attempts=3)
        time.sleep(0.05)
        cb.assert_called_once()
        permissions.cancel_permission_retry()

    def test_schedule_permission_retry_calls_patched_cancel(self, monkeypatch):
        """``schedule_permission_retry`` (in checker) calls
        ``_p.cancel_permission_retry()`` to cancel any existing timer —
        a monkeypatch on ``permissions.cancel_permission_retry`` must be
        observed."""
        from unittest.mock import MagicMock

        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "check_keyboard_permission",
            lambda: permissions.PermissionState.DENIED,
        )
        cancel_called = []
        monkeypatch.setattr(
            permissions,
            "cancel_permission_retry",
            lambda: cancel_called.append("cancelled"),
        )
        try:
            permissions.schedule_permission_retry(MagicMock(), interval=10.0, max_attempts=1)
            # ``schedule_permission_retry`` must have called the patched
            # ``cancel_permission_retry`` (the patched version appends to
            # cancel_called).
            assert cancel_called == ["cancelled"], (
                "schedule_permission_retry did not call the patched cancel_permission_retry — "
                "function-patch propagation is broken."
            )
        finally:
            # Restore the real cancel_permission_retry on the facade and
            # call it to clean up the timer.
            from voice_typer.server.permissions.checker import cancel_permission_retry as real_cancel

            monkeypatch.setattr(permissions, "cancel_permission_retry", real_cancel)
            permissions.cancel_permission_retry()

    def test_check_microphone_permission_dispatches_to_patched_probe(self, monkeypatch):
        """``check_microphone_permission`` (in checker) calls
        ``_p._check_macos_microphone()`` (etc.) — a monkeypatch on
        ``permissions._check_macos_microphone`` must be observed."""
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: False)
        monkeypatch.setattr(permissions, "is_macos", lambda: True)
        monkeypatch.setattr(permissions, "is_linux", lambda: False)
        monkeypatch.setattr(
            permissions,
            "_check_macos_microphone",
            lambda: permissions.MicrophonePermissionState.DENIED,
        )
        result = permissions.check_microphone_permission()
        assert result == permissions.MicrophonePermissionState.DENIED

    def test_verify_microphone_accessible_calls_patched_check(self, monkeypatch):
        """``verify_microphone_accessible`` (in checker) calls
        ``_p.check_microphone_permission()`` — a monkeypatch on
        ``permissions.check_microphone_permission`` must be observed."""
        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.GRANTED,
        )
        # Should NOT raise on GRANTED.
        permissions.verify_microphone_accessible()

    def test_request_microphone_permission_calls_patched_helpers(self, monkeypatch):
        """``request_microphone_permission`` (in checker) calls
        ``_p._open_macos_microphone_settings()`` and
        ``_p._trigger_macos_microphone_consent_prompt()`` — monkeypatches
        on those facade attributes must be observed."""
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: False)
        monkeypatch.setattr(permissions, "is_macos", lambda: True)
        monkeypatch.setattr(permissions, "is_linux", lambda: False)
        called = []
        monkeypatch.setattr(
            permissions,
            "_open_macos_microphone_settings",
            lambda: called.append("opened"),
        )
        monkeypatch.setattr(
            permissions,
            "_trigger_macos_microphone_consent_prompt",
            lambda: called.append("triggered"),
        )
        monkeypatch.setattr(permissions, "schedule_permission_retry", lambda cb, **kw: None)

        permissions.request_microphone_permission()
        assert called == ["opened", "triggered"]

    def test_open_linux_pkexec_prompt_calls_patched_find_script(self, monkeypatch):
        """``_open_linux_pkexec_prompt`` (in filesystem) calls
        ``_p._find_linux_install_script()`` — a monkeypatch on
        ``permissions._find_linux_install_script`` must be observed."""
        from pathlib import Path

        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "_find_linux_install_script",
            lambda: Path("/fake/install_permissions.py"),
        )
        # Force ``shutil.which('pkexec')`` to return None so the prompt
        # logs an error and returns without invoking subprocess.
        monkeypatch.setattr(permissions.shutil, "which", lambda cmd: None)
        # Should not raise, just log.
        permissions._open_linux_pkexec_prompt()


# ── 4. Package structure ─────────────────────────────────────────────


class TestPackageStructure:
    """The ``permissions`` package must have the 4 submodules specified
    in the split plan."""

    EXPECTED_SUBMODULES = [
        "checker",
        "mic",
        "accessibility",
        "filesystem",
    ]

    def test_all_submodules_exist(self):
        import voice_typer.server.permissions as p

        # __path__ is set on packages (not modules).
        assert hasattr(p, "__path__"), (
            "permissions should be a package (directory with __init__.py), not a single .py module"
        )
        pkg_dir = Path(p.__file__).parent
        for sub in self.EXPECTED_SUBMODULES:
            assert (pkg_dir / f"{sub}.py").exists(), f"Submodule {sub}.py is missing from the permissions package"

    def test_facade_is_not_the_old_monolith(self):
        """The facade __init__.py must be a thin re-export layer, not
        the original 1144-LOC monolith. Allow up to ~300 LOC for the
        state declarations + docstrings + re-export imports (mirrors
        the crash_handler split's test_facade_is_not_the_old_monolith
        test)."""
        import voice_typer.server.permissions as p

        init_path = Path(p.__file__)
        loc = len(init_path.read_text().splitlines())
        # The facade holds mutable state + re-exports — allow up to ~250
        # LOC for the state declarations + docstrings + re-export
        # imports. The original was 1144 LOC; the facade must be
        # substantially smaller.
        assert loc < 300, (
            f"permissions/__init__.py is {loc} LOC — expected a thin facade "
            f"(<300 LOC). The original monolith was 1144 LOC; the facade must "
            f"only hold mutable state + re-exports."
        )

    def test_old_monolith_removed(self):
        """The old ``permissions.py`` file must be removed (replaced by
        the package)."""
        import voice_typer.server.permissions as p

        pkg_dir = Path(p.__file__).parent
        old_file = pkg_dir.parent / "permissions.py"
        assert not old_file.exists(), (
            f"{old_file} still exists — the old monolith must be removed "
            f"after the split to avoid shadowing the package."
        )


# ── 5. Functional smoke test (Linux-runnable surface) ────────────────


class TestFunctionalSmoke:
    """Quick functional smoke test on the Linux-runnable surface to
    verify the split didn't break basic behavior."""

    def test_permission_error_classifier_recognizes_accessibility(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert permission_error_is_permission_denied("Accessibility permission required.") is True

    def test_permission_error_classifier_recognizes_linux_input(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert permission_error_is_permission_denied("Permission denied opening /dev/input/event0") is True

    def test_permission_error_classifier_rejects_unrelated_error(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert permission_error_is_permission_denied("Invalid hotkey spec") is False

    def test_permission_error_classifier_rejects_empty(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert permission_error_is_permission_denied("") is False

    def test_permission_error_classifier_rejects_none(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert permission_error_is_permission_denied(None) is False  # type: ignore[arg-type]

    def test_cancel_permission_retry_is_safe_when_no_timer(self):
        from voice_typer.server.permissions import cancel_permission_retry

        # Should not raise even if no timer is pending.
        cancel_permission_retry()

    def test_initial_pyobjc_cache_is_none(self):
        """The cache must default to ``None`` (not yet probed)."""
        from voice_typer.server import permissions

        # Reset to ensure a clean state.
        permissions.reset_pyobjc_cache()
        assert permissions._PYOBJC_AVAILABLE is None

    def test_reset_pyobjc_cache_sets_none(self):
        from voice_typer.server import permissions

        permissions._PYOBJC_AVAILABLE = True
        permissions.reset_pyobjc_cache()
        assert permissions._PYOBJC_AVAILABLE is None

    def test_permission_state_enum_values(self):
        """The enum values must match the original values exactly."""
        from voice_typer.server.permissions import PermissionState

        assert PermissionState.GRANTED.value == "granted"
        assert PermissionState.DENIED.value == "denied"
        assert PermissionState.UNKNOWN.value == "unknown"
        assert PermissionState.ERROR.value == "error"

    def test_microphone_permission_state_enum_values(self):
        from voice_typer.server.permissions import MicrophonePermissionState

        assert MicrophonePermissionState.GRANTED.value == "granted"
        assert MicrophonePermissionState.DENIED.value == "denied"
        assert MicrophonePermissionState.PROMPT.value == "prompt"
        assert MicrophonePermissionState.UNKNOWN.value == "unknown"

    def test_find_linux_install_script_returns_path_or_none(self):
        """``_find_linux_install_script`` returns a ``Path`` (dev mode) or
        ``None`` (installed package); never raises."""
        from voice_typer.server.permissions import _find_linux_install_script

        result = _find_linux_install_script()
        if result is not None:
            from pathlib import Path

            assert isinstance(result, Path)
            assert result.name == "install_permissions.py"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
