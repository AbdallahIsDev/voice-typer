"""Windows-specific tests for ``voice_typer.server.container_detect``.

TEST-GAP-1 (2026-07-18): The cross-platform test file
``tests/test_container_detect.py`` already covers the platform-gating
behavior (non-Linux always returns False / None) via a single
``monkeypatch.setattr("sys.platform", "win32")`` call per test. This
file extends that coverage with Windows-specific scenarios that the
cross-platform file does not exercise:

* Windows path-separator robustness: the SUT constructs POSIX-style
  absolute paths (``/.dockerenv``, ``/run/.containerenv``,
  ``/proc/1/cgroup``). On a real win32 runner these ``Path`` objects
  have backslash str-reprs (``"\\.dockerenv"`` etc.). The cross-
  platform test helpers normalize via ``PurePosixPath`` so the patched
  ``Path.exists`` / ``Path.read_text`` semantics match the SUT's
  ``Path("/.dockerenv").exists()`` call on every runner. This file
  explicitly asserts the normalization invariant.

* Windows env-var gate: on win32 the SUT short-circuits before reading
  ``CONTAINER`` env var, so a stale ``CONTAINER=systemd-nspawn`` value
  set by a previous Linux boot (e.g. dual-boot machine) MUST NOT cause
  a false positive. This is a real user-facing scenario.

* Windows registry / WSL2 detection: out of scope (the SUT does not
  detect WSL2; that's documented in the module docstring as a known
  limitation). This file documents the gap with an explicit test.

These tests are platform-agnostic in execution — they patch
``sys.platform`` to ``"win32"`` so they run on any CI runner. They
exist as a separate file so a Windows-host CI job can run them as a
focused suite without the Linux-specific cgroup tests.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

import pytest
from voice_typer.server import container_detect

_LOGGER_NAME = "voice_typer.server.container_detect"


# ── shared helpers (mirror tests/test_container_detect.py) ────────────


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


# ── Windows path-separator robustness ─────────────────────────────────


class TestWin32PathSeparatorRobustness:
    """The SUT uses POSIX-style absolute paths (``/.dockerenv`` etc.).
    On win32 these ``Path`` instances have backslash str-reprs. The
    test helpers MUST normalize via ``PurePosixPath`` so the patched
    ``Path.exists`` matches the SUT's call on every runner. These
    tests pin the invariant."""

    def test_normalize_returns_forward_slashes_on_win32_runner(self, monkeypatch):
        """If this test runs on a win32 host, ``_normalize`` still returns
        forward-slash strings (not backslashes). On Linux/macOS runners
        this is trivially true; on win32 it's the actual safety net."""
        _force_win32(monkeypatch)
        assert _normalize("/.dockerenv") == "/.dockerenv"
        assert _normalize("/run/.containerenv") == "/run/.containerenv"
        assert _normalize("/proc/1/cgroup") == "/proc/1/cgroup"

    def test_normalize_path_object_returns_forward_slashes(self, monkeypatch):
        """``_normalize(Path("/.dockerenv"))`` returns ``/.dockerenv``
        regardless of how the runner's ``Path`` renders its str form."""
        _force_win32(monkeypatch)
        assert _normalize(Path("/.dockerenv")) == "/.dockerenv"
        assert _normalize(Path("/proc/1/cgroup")) == "/proc/1/cgroup"

    def test_dockerenv_detection_works_when_patched_on_win32(self, monkeypatch):
        """Even on a win32 runner, patching ``Path.exists`` for
        ``/.dockerenv`` and forcing ``sys.platform = "linux"`` causes
        the SUT to detect a Docker container. This proves the path-
        comparison helper is not silently no-op on win32 runners."""
        monkeypatch.setattr("sys.platform", "linux")
        _set_existing_paths(monkeypatch, {"/.dockerenv"})
        _set_cgroup(monkeypatch, "0::/\n")
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is True
        assert container_detect.get_container_type() == "docker"


# ── Windows platform gate ─────────────────────────────────────────────


class TestWin32PlatformGate:
    """On win32, all detection paths MUST short-circuit before touching
    the filesystem or env vars. The SUT's first check is
    ``sys.platform.startswith("linux")``; if False, return False / None."""

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
        """A dual-boot machine may have ``CONTAINER=systemd-nspawn`` set
        in the system env (carried over from a Linux session). The win32
        platform gate MUST short-circuit before the env-var check, so
        the stale value does NOT cause a false positive."""
        _force_win32(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "0::/system.slice/docker.service\n")
        monkeypatch.setenv("CONTAINER", "systemd-nspawn")
        assert container_detect.is_in_container() is False
        assert container_detect.get_container_type() is None

    def test_win32_with_all_indicators_present_still_returns_false(self, monkeypatch):
        """Stress-test: even with ALL Linux indicators present, the
        platform gate wins. This is the user-facing guarantee: Voice
        Typer on Windows never claims to be in a container."""
        _force_win32(monkeypatch)
        _set_existing_paths(monkeypatch, {"/.dockerenv", "/run/.containerenv"})
        _set_cgroup(monkeypatch, "0::/system.slice/docker.service\n")
        monkeypatch.setenv("CONTAINER", "systemd-nspawn")
        assert container_detect.is_in_container() is False
        assert container_detect.get_container_type() is None

    def test_win32_does_not_read_proc_filesystem(self, monkeypatch):
        """On win32, the SUT MUST NOT call ``Path("/proc/1/cgroup").read_text()``
        because ``/proc`` does not exist. We assert this by patching
        ``Path.read_text`` to raise if called, and verifying the SUT
        returns False without triggering the raise."""
        _force_win32(monkeypatch)
        _set_existing_paths(monkeypatch, set())

        def _read_text_must_not_be_called(self: Path, *args, **kwargs):
            raise AssertionError(
                "SUT must not read /proc on win32 — platform gate should short-circuit before any filesystem access."
            )

        monkeypatch.setattr(Path, "read_text", _read_text_must_not_be_called)
        _clear_container_env(monkeypatch)
        # Must not raise — proves the SUT short-circuits before reading.
        assert container_detect.is_in_container() is False
        assert container_detect.get_container_type() is None


# ── Windows registry / WSL2 detection gap (documented) ────────────────


class TestWin32WSL2GapDocumented:
    """The SUT does NOT detect WSL2 containers on win32. This is a
    documented gap — WSL2 detection would require reading the Windows
    registry or calling ``wsl.exe --status``, which is out of scope
    for the current ``container_detect`` module (its job is to warn
    about missing tray/audio/GPU/hotkey features, all of which work
    natively on win32 regardless of WSL2). These tests pin the gap
    so a future agent doesn't accidentally add WSL2 detection without
    updating the module docstring."""

    def test_wsl2_indicator_does_not_cause_false_positive(self, monkeypatch):
        """A WSL2 indicator (e.g. ``WSL_DISTRO_NAME`` env var) MUST NOT
        trigger detection, because the SUT is documented to detect
        only Linux containers. Voice Typer running on win32 host (not
        inside WSL2) should never claim to be in a container."""
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
