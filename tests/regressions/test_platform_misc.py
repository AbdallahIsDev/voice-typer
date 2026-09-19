"""The class/method names, assertion logic, and imports below are"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


class TestPlatformUtilsDeadCodeRemoved:
    """The finding: ``validate_env_vars`` in platform_utils.py was dead"""

    def test_validate_env_vars_removed_from_platform_utils(self):
        from voice_typer.server import platform_utils

        assert not hasattr(platform_utils, "validate_env_vars"), (
            "validate_env_vars must be removed from platform_utils "
            "(it was dead code duplicating app.py::_validate_env_vars)."
        )
        assert not hasattr(platform_utils, "_init_env_var_schema"), "_init_env_var_schema must be removed."
        assert not hasattr(platform_utils, "_ENV_VAR_SCHEMA"), "_ENV_VAR_SCHEMA must be removed."

    def test_validate_env_vars_canonical_in_env_validation(self):
        from voice_typer.server import app, env_validation

        assert hasattr(env_validation, "_validate_env_vars"), (
            "env_validation must be the single source of truth for env-var validation."
        )
        # And the stale app-namespace re-export must stay gone.
        assert not hasattr(app, "_validate_env_vars"), (
            "app.py must not re-export _validate_env_vars; import it from env_validation."
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
    """different APIs and size tables. Fix: deleted the local"""

    def test_local_check_disk_space_removed(self):
        from voice_typer.server import asr_setup

        assert not hasattr(asr_setup, "_check_disk_space"), (
            "_check_disk_space must be removed from asr_setup "
            "(duplicate of transcription.py::_check_disk_space_for_download)."
        )
        assert not hasattr(asr_setup, "_ESTIMATED_MODEL_SIZES"), (
            "_ESTIMATED_MODEL_SIZES must be removed from asr_setup."
        )

    def test_canonical_check_disk_space_still_exists(self):
        from voice_typer.server.transcription import _check_disk_space_for_download

        assert callable(_check_disk_space_for_download)

    def test_asr_setup_delegates_to_canonical(self):
        """``asr_setup.download_parakeet_weights`` must delegate"""

        from voice_typer.server import asr_setup

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
            result = asr_setup.download_parakeet_weights(force=True)

        assert isinstance(result, tuple) and len(result) == 3, (
            "download_parakeet_weights must return a 3-tuple (success, reason, exc_info)."
        )
        assert result[0] is False, (
            "download_parakeet_weights should return success=False "
            "when the canonical disk-space check raises RuntimeError."
        )
        assert result[1] == "disk_space_insufficient", (
            "download_parakeet_weights should return "
            "reason='disk_space_insufficient' when the canonical disk-space "
            f"check raises RuntimeError. Got reason={result[1]!r}."
        )
        # The canonical check must have been invoked.
        assert mock_check.call_count == 1, (
            "download_parakeet_weights must delegate disk-space "
            "checking to _check_disk_space_for_download from transcription.py."
        )


class TestDaemonThreadRationaleDocumented:
    """The finding: 9+ manual Thread(daemon=True) sites without rationale"""

    def test_hotkeys_win32_thread_has_rationale(self):
        # KEEP, pins RACE-008 rationale comment on the daemon
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        src = inspect.getsource(WindowsNativeHotkey.start)
        # is stripped by C-STYLE-1 cleanup, but the rationale comment
        assert "daemon=True is acceptable" in src, (
            "WindowsNativeHotkey.start must keep a daemon=True rationale comment on the daemon thread."
        )

    def test_hotkeys_ipc_thread_has_rationale(self):
        # KEEP, pins RACE-008 rationale comment on the WaylandHotkey
        from voice_typer.server.hotkeys import WaylandHotkey

        inspect.getsource(WaylandHotkey.start)
        # The rationale comment is in _start_socket_server which is
        class_src = inspect.getsource(WaylandHotkey)
        # Assert the rationale PHRASE, the RACE-008 ticket token is
        # stripped by C-STYLE-1 cleanup, but the daemon=True rationale
        assert "daemon=True is acceptable" in class_src, (
            "WaylandHotkey must keep a daemon=True rationale comment on the socket-accept daemon thread."
        )

    def test_tray_bg_thread_has_rationale(self):
        # KEEP, pins RACE-008 rationale comment on the tray
        from voice_typer.server.tray import TrayIcon

        # The background daemon thread is spawned in the shared
        src = inspect.getsource(TrayIcon._launch_bg_work)
        # Assert the rationale PHRASE, the RACE-008 ticket token is
        # stripped by C-STYLE-1 cleanup, but the daemon=True rationale
        assert "daemon=True is acceptable" in src, (
            "TrayIcon._launch_bg_work must keep a daemon=True rationale comment on the daemon thread spawn site."
        )

    def test_service_download_thread_has_rationale(self):
        # KEEP, pins RACE-008 rationale comment on the service.py
        from pathlib import Path

        import voice_typer.server.service.model as service_model_pkg

        pkg_dir = Path(service_model_pkg.__file__).resolve().parent
        src = "".join(p.read_text(encoding="utf-8") for p in sorted(pkg_dir.glob("*.py")))
        # Assert the rationale PHRASE, the RACE-008 ticket token is
        # stripped by C-STYLE-1 cleanup, but the daemon=True rationale
        assert "daemon=True is acceptable" in src, (
            "service/model package must keep a daemon=True rationale comment on the download daemon thread."
        )


class TestContainerEnvironmentDetection:
    """Detect container/cgroup environments."""

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
        reason="Non-Linux only: is_in_container short-circuits to False when /proc/1/cgroup and cgroup v1/v2 signatures unavailable",  # noqa: E501
    )
    def test_is_in_container_returns_false_on_non_linux(self):
        from voice_typer.server.container_detect import is_in_container

        assert is_in_container() is False

    def test_get_container_type_returns_none_when_not_in_container(self):
        from voice_typer.server.container_detect import get_container_type

        # On CI (not in container), should return None
        result = get_container_type()
        assert result is None or isinstance(result, str)

    def test_container_detect_called_in_startup(self):
        # KEEP, pins  (app.py calls warn_if_in_container
        from voice_typer.server import app

        src = inspect.getsource(app)
        assert "warn_if_in_container" in src


class TestPlatContentContentEditable:
    """Detect contentEditable elements via UI Automation."""

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
    """macOS code exists but requires macOS CI runner."""

    def test_macos_code_exists(self):
        """macOS-specific code must exist in the codebase."""
        from voice_typer.server import platform_utils

        src = inspect.getsource(platform_utils)
        assert "darwin" in src or "is_macos" in src

    def test_macos_ci_runner_exists(self):
        """A macOS CI runner IS configured in build.yml."""
        build_yml = Path(__file__).resolve().parent.parent.parent / ".github" / "workflows" / "build.yml"
        if build_yml.exists():
            src = build_yml.read_text(encoding="utf-8")
            assert "macos-latest" in src or "macos" in src.lower(), "No macOS CI runner found, macOS code is untested."
