"""Windows-specific tests for ``voice_typer.server.container_detect``."""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

import pytest
from voice_typer.server import container_detect

_LOGGER_NAME = "voice_typer.server.container_detect"


def _normalize(p) -> str:
    """Normalize a path-like to a POSIX-style string (forward slashes)."""
    if isinstance(p, str):
        return str(PurePosixPath(p))
    return p.as_posix() if hasattr(p, "as_posix") else str(PurePosixPath(str(p)))


def _force_win32(monkeypatch) -> None:
    """Force the SUT to behave as if running on Windows."""
    monkeypatch.setattr("sys.platform", "win32")


def _set_existing_paths(monkeypatch, existing: set[str]) -> None:
    """Mock ``Path.exists()`` to return True only for the given POSIX paths."""
    existing_normalized = {_normalize(p) for p in existing}

    def _exists(self: Path) -> bool:
        return _normalize(self) in existing_normalized

    monkeypatch.setattr(Path, "exists", _exists)


def _set_cgroup(monkeypatch, content: str | None) -> None:
    """Mock ``Path.read_text()`` to return ``content`` for ``/proc/1/cgroup``."""
    cgroup_posix = _normalize(Path("/proc/1/cgroup"))

    def _read_text(self: Path, *args, **kwargs) -> str:
        if _normalize(self) == cgroup_posix:
            if content is None:
                raise OSError("cannot read /proc/1/cgroup")
            return content
        raise FileNotFoundError(str(self))

    monkeypatch.setattr(Path, "read_text", _read_text)


def _clear_container_env(monkeypatch) -> None:
    monkeypatch.delenv("CONTAINER", raising=False)


class TestWin32PathSeparatorRobustness:
    """The SUT uses POSIX-style absolute paths (``/.dockerenv`` etc.)."""

    def test_normalize_returns_forward_slashes_on_win32_runner(self, monkeypatch):
        """If this test runs on a win32 host, ``_normalize`` still returns"""
        _force_win32(monkeypatch)
        assert _normalize("/.dockerenv") == "/.dockerenv"
        assert _normalize("/run/.containerenv") == "/run/.containerenv"
        assert _normalize("/proc/1/cgroup") == "/proc/1/cgroup"

    def test_normalize_path_object_returns_forward_slashes(self, monkeypatch):
        """``_normalize(Path(\"/.dockerenv\"))`` returns ``/.dockerenv``"""
        _force_win32(monkeypatch)
        assert _normalize(Path("/.dockerenv")) == "/.dockerenv"
        assert _normalize(Path("/proc/1/cgroup")) == "/proc/1/cgroup"

    def test_dockerenv_detection_works_when_patched_on_win32(self, monkeypatch):
        """``/.dockerenv`` and forcing ``sys.platform = \"linux\"`` causes"""
        monkeypatch.setattr("sys.platform", "linux")
        _set_existing_paths(monkeypatch, {"/.dockerenv"})
        _set_cgroup(monkeypatch, "0::/\n")
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is True
        assert container_detect.get_container_type() == "docker"


class TestWin32PlatformGate:
    """On win32, all detection paths MUST short-circuit before touching"""

    def test_is_in_container_returns_false_on_win32(self, monkeypatch):
        _force_win32(monkeypatch)
        _set_existing_paths(monkeypatch, {"/.dockerenv"})
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is False

    def test_get_container_type_returns_none_on_win32(self, monkeypatch):
        _force_win32(monkeypatch)
        _set_existing_paths(monkeypatch, {"/.dockerenv"})
        _clear_container_env(monkeypatch)
        assert container_detect.get_container_type() is None

    def test_warn_if_in_container_no_warning_on_win32(self, monkeypatch, caplog):
        _force_win32(monkeypatch)
        _set_existing_paths(monkeypatch, {"/.dockerenv"})
        _clear_container_env(monkeypatch)
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            container_detect.warn_if_in_container()
        assert not any("[CONTAINER]" in r.message for r in caplog.records)

    def test_win32_with_stale_container_env_var_does_not_detect(self, monkeypatch):
        """A dual-boot machine may have ``CONTAINER=systemd-nspawn`` set"""
        _force_win32(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "0::/system.slice/docker.service\n")
        monkeypatch.setenv("CONTAINER", "systemd-nspawn")
        assert container_detect.is_in_container() is False
        assert container_detect.get_container_type() is None

    def test_win32_with_all_indicators_present_still_returns_false(self, monkeypatch):
        """platform gate wins. This is the user-facing guarantee: Voice"""
        _force_win32(monkeypatch)
        _set_existing_paths(monkeypatch, {"/.dockerenv", "/run/.containerenv"})
        _set_cgroup(monkeypatch, "0::/system.slice/docker.service\n")
        monkeypatch.setenv("CONTAINER", "systemd-nspawn")
        assert container_detect.is_in_container() is False
        assert container_detect.get_container_type() is None

    def test_win32_does_not_read_proc_filesystem(self, monkeypatch):
        """On win32, the SUT MUST NOT call ``Path(\"/proc/1/cgroup\").read_text()``"""
        _force_win32(monkeypatch)
        _set_existing_paths(monkeypatch, set())

        def _read_text_must_not_be_called(self: Path, *args, **kwargs):
            raise AssertionError(
                "SUT must not read /proc on win32, platform gate should short-circuit before any filesystem access."
            )

        monkeypatch.setattr(Path, "read_text", _read_text_must_not_be_called)
        _clear_container_env(monkeypatch)
        # Must not raise, proves the SUT short-circuits before reading.
        assert container_detect.is_in_container() is False
        assert container_detect.get_container_type() is None


class TestWin32WSL2GapDocumented:
    """documented gap, WSL2 detection would require reading the Windows"""

    def test_wsl2_indicator_does_not_cause_false_positive(self, monkeypatch):
        """A WSL2 indicator (e.g. ``WSL_DISTRO_NAME`` env var) MUST NOT"""
        _force_win32(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _clear_container_env(monkeypatch)
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-22.04")
        monkeypatch.setenv("WSLENV", "PATH/l")
        assert container_detect.is_in_container() is False
        assert container_detect.get_container_type() is None


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v", "--no-cov", "-q"]))
