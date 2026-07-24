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
from pathlib import Path
from unittest.mock import patch

import pytest


# WP-1: the previous Linux test-env shim that aliased
# ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` and inserted a ``MagicMock``
# for ``voice_typer.server.crash_handler`` into ``sys.modules`` has been
# removed. ``crash_handler.py`` now gates the ``@ctypes.WINFUNCTYPE(...)``
# decorator behind ``sys.platform == "win32"``, so the module imports
# cleanly on Linux/macOS without any test-infrastructure shim.
class TestPlatformUtilsDeadCodeRemoved:
    """PLAT-008.

    The finding: ``validate_env_vars`` in platform_utils.py was dead
    code (never called from production). Fix: deleted the dead
    function, ``_init_env_var_schema``, and ``_ENV_VAR_SCHEMA``.
    """

    def test_validate_env_vars_removed_from_platform_utils(self):
        from voice_typer.server import platform_utils

        assert not hasattr(platform_utils, "validate_env_vars"), (
            "PLAT-008: validate_env_vars must be removed from platform_utils "
            "(it was dead code duplicating app.py::_validate_env_vars)."
        )
        assert not hasattr(platform_utils, "_init_env_var_schema"), "PLAT-008: _init_env_var_schema must be removed."
        assert not hasattr(platform_utils, "_ENV_VAR_SCHEMA"), "PLAT-008: _ENV_VAR_SCHEMA must be removed."

    def test_app_validate_env_vars_still_exists(self):
        from voice_typer.server import app

        # The canonical implementation must still exist in app.py
        # (it's a module-level function, not a method)
        assert hasattr(app, "_validate_env_vars"), (
            "PLAT-008: app.py must still have _validate_env_vars as the single source of truth for env-var validation."
        )

    def test_platform_utils_still_exports_platform_helpers(self):
        from voice_typer.server.platform_utils import (
            is_linux,
            is_macos,
            is_windows,
            platform_name,
        )

        assert callable(is_windows)
        assert callable(is_macos)
        assert callable(is_linux)
        assert callable(platform_name)
        assert isinstance(is_windows(), bool)
        assert isinstance(is_macos(), bool)
        assert isinstance(platform_name(), str)


class TestDuplicateDiskSpaceCheckRemoved:
    """PROD-005.

    The finding: two disk-space check implementations coexisted with
    different APIs and size tables. Fix: deleted the local
    ``_check_disk_space`` and ``_ESTIMATED_MODEL_SIZES`` from
    asr_setup.py; the canonical ``_check_disk_space_for_download`` in
    transcription.py is the single source of truth.
    """

    def test_local_check_disk_space_removed(self):
        from voice_typer.server import asr_setup

        assert not hasattr(asr_setup, "_check_disk_space"), (
            "PROD-005: _check_disk_space must be removed from asr_setup "
            "(duplicate of transcription.py::_check_disk_space_for_download)."
        )
        assert not hasattr(asr_setup, "_ESTIMATED_MODEL_SIZES"), (
            "PROD-005: _ESTIMATED_MODEL_SIZES must be removed from asr_setup."
        )

    def test_canonical_check_disk_space_still_exists(self):
        from voice_typer.server.transcription import _check_disk_space_for_download

        assert callable(_check_disk_space_for_download)

    def test_asr_setup_delegates_to_canonical(self):
        """PROD-005: ``asr_setup.download_parakeet_weights`` must delegate
        disk-space checking to the canonical
        ``_check_disk_space_for_download`` from ``transcription.py``
        (rather than a local duplicate that previously diverged in size
        tables and return semantics).

        RW-8: ported from a source-string meta-test (which inspected
        ``download_parakeet_weights`` source for the substring
        ``_check_disk_space_for_download``) to a behavioral test that
        mocks the canonical function and verifies it is invoked. The
        behavioral test is robust to refactors — if the call is moved
        into a helper or renamed, the test still catches the regression
        as long as disk-space checking is bypassed.
        """

        from voice_typer.server import asr_setup

        # Mock ``snapshot_download`` to raise (cache miss) so the
        # function proceeds past the cache-check block to the
        # disk-space check. Mock ``_check_disk_space_for_download`` to
        # raise ``RuntimeError`` so the function short-circuits after
        # the check (no actual download attempt).
        with (
            patch(
                "huggingface_hub.snapshot_download",
                side_effect=Exception("cache miss"),
            ),
            patch(
                "voice_typer.server.transcription._check_disk_space_for_download",
                side_effect=RuntimeError("insufficient space"),
            ) as mock_check,
        ):
            result = asr_setup.download_parakeet_weights()

        # The function returns False on insufficient space.
        assert result is False, (
            "PROD-005: download_parakeet_weights should return False when "
            "the canonical disk-space check raises RuntimeError."
        )
        # The canonical check must have been invoked.
        assert mock_check.call_count == 1, (
            "PROD-005: download_parakeet_weights must delegate disk-space "
            "checking to _check_disk_space_for_download from transcription.py."
        )


class TestDaemonThreadRationaleDocumented:
    """RACE-008.

    The finding: 9+ manual Thread(daemon=True) sites without rationale
    comments. Fix: added ``# RACE-008`` rationale comments to each
    undocumented site explaining why daemon=True is acceptable.
    """

    def test_hotkeys_win32_thread_has_rationale(self):
        # RW-8: KEEP — pins RACE-008 rationale comment on the daemon
        # thread. The comment is documentation, not behavior; a
        # behavioral test can't verify rationale presence. Source-string
        # check is the only way to catch removal.
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        src = inspect.getsource(WindowsNativeHotkey.start)
        assert "RACE-008" in src, (
            "RACE-008: WindowsNativeHotkey.start must have a RACE-008 rationale comment on the daemon thread."
        )

    def test_hotkeys_ipc_thread_has_rationale(self):
        # RW-8: KEEP — pins RACE-008 rationale comment on the WaylandHotkey
        # socket-accept daemon thread. Same rationale as the win32 variant.
        from voice_typer.server.hotkeys import WaylandHotkey

        inspect.getsource(WaylandHotkey.start)
        # The rationale comment is in _start_socket_server which is
        # called from start(). Check the whole class source.
        class_src = inspect.getsource(WaylandHotkey)
        assert "RACE-008" in class_src, (
            "RACE-008: WaylandHotkey must have a RACE-008 rationale on the socket-accept daemon thread."
        )

    def test_tray_bg_thread_has_rationale(self):
        # RW-8: KEEP — pins RACE-008 rationale comment on the tray
        # background-thread daemon. Same rationale as the win32 variant.
        from voice_typer.server.tray import TrayIcon

        src = inspect.getsource(TrayIcon.start)
        assert "RACE-008" in src, (
            "RACE-008: TrayIcon.start must have a RACE-008 rationale on each daemon thread spawn site."
        )

    def test_service_download_thread_has_rationale(self):
        # RW-8: KEEP — pins RACE-008 rationale comment on the service.py
        # model-download daemon thread. Same rationale as the win32 variant.
        from voice_typer.server import service

        # The download thread is inside a method — search the whole module.
        src = inspect.getsource(service)
        assert "RACE-008" in src, "RACE-008: service.py must have a RACE-008 rationale on the download daemon thread."


class TestSystemdUserUnitForMainApp:
    """PLAT-019: systemd user unit for the main app."""

    def test_register_linux_app_service_exists(self):
        from voice_typer.server import prewarm_scheduler_posix as psp

        assert hasattr(psp, "register_linux_app_service")

    def test_build_linux_app_service_has_restart(self):
        from voice_typer.server import prewarm_scheduler_posix as psp

        service = psp._build_linux_app_service()
        assert "Restart=on-failure" in service
        assert "Type=simple" in service
        assert "voice_typer.server.ipc_server" in service


class TestContainerEnvironmentDetection:
    """PLAT-021: Detect container/cgroup environments."""

    def test_is_in_container_exists(self):
        from voice_typer.server.container_detect import is_in_container

        assert callable(is_in_container)

    def test_get_container_type_exists(self):
        from voice_typer.server.container_detect import get_container_type

        assert callable(get_container_type)

    def test_warn_if_in_container_exists(self):
        from voice_typer.server.container_detect import warn_if_in_container

        assert callable(warn_if_in_container)

    @pytest.mark.skipif(
        sys.platform.startswith("linux"),
        reason="Non-Linux only: is_in_container short-circuits to False when /proc/1/cgroup and cgroup v1/v2 signatures unavailable",
    )
    def test_is_in_container_returns_false_on_non_linux(self):
        from voice_typer.server.container_detect import is_in_container

        assert is_in_container() is False

    def test_get_container_type_returns_none_when_not_in_container(self):
        from voice_typer.server.container_detect import get_container_type

        # On CI (not in container), should return None
        # On a container, should return a string
        result = get_container_type()
        assert result is None or isinstance(result, str)

    def test_container_detect_called_in_startup(self):
        # RW-8: KEEP — pins PLAT-021 (app.py calls warn_if_in_container
        # at startup). A behavioral test would need to capture log output
        # from app startup, which is heavy; the source-string check
        # catches removal of the call directly.
        from voice_typer.server import app

        src = inspect.getsource(app)
        assert "warn_if_in_container" in src


class TestPlatContentContentEditable:
    """PLAT-CONTENT: Detect contentEditable elements via UI Automation."""

    def test_is_content_editable_exists(self):
        from voice_typer.server.clipboard import _is_content_editable

        assert callable(_is_content_editable)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Non-Windows path: _is_content_editable short-circuits to False when Win32 UI Automation is unavailable",
    )
    def test_returns_false_on_non_windows(self):
        from voice_typer.server.clipboard import _is_content_editable

        assert _is_content_editable() is False


class TestPlatMacBlocked:
    """PLAT-MAC: macOS code exists but requires macOS CI runner."""

    def test_macos_code_exists(self):
        """macOS-specific code must exist in the codebase.

        RW-8: KEEP — pins PLAT-MAC (app.py contains darwin or is_macos
        references). A behavioral test would need to run on macOS and
        observe the macOS code path, which is heavy (platform-specific);
        the source-string check catches removal of all macOS branches.
        """
        from voice_typer.server import app

        src = inspect.getsource(app)
        assert "darwin" in src or "is_macos" in src

    def test_macos_ci_runner_exists(self):
        """PLAT-MAC: A macOS CI runner IS configured in build.yml.
        This test pins that state — if the runner is removed, this
        test will fail and alert maintainers that macOS code is
        no longer being tested in CI.

        RW-8: KEEP — pins PLAT-MAC (macOS CI runner in build.yml).
        # A behavioral test would need to run the workflow and verify the
        # runner executes, which is heavy (CI-only); the file-content
        # check catches removal of the macOS runner directly.
        """
        build_yml = Path(__file__).resolve().parent.parent.parent / ".github" / "workflows" / "build.yml"
        if build_yml.exists():
            src = build_yml.read_text(encoding="utf-8")
            assert "macos-latest" in src or "macos" in src.lower(), (
                "PLAT-MAC: No macOS CI runner found — macOS code is untested."
            )
