"""Real unit tests for ``voice_typer.server.container_detect``.

PLAT-021: Container/cgroup detection for Linux deployments.

These tests pin the behavior of all three public callables
(``is_in_container``, ``get_container_type``, ``warn_if_in_container``)
across every documented detection branch:

* platform gating (non-Linux always returns False / None)
* ``/.dockerenv`` (Docker)
* ``/run/.containerenv`` (Podman)
* ``CONTAINER`` env var (systemd-nspawn), incl. empty-string edge case
* ``/proc/1/cgroup`` signatures: docker, lxc, kubepods, containerd
* OSError / PermissionError swallowing when ``/proc/1/cgroup`` is unreadable
* empty ``/proc/1/cgroup`` content
* detection precedence in ``get_container_type``
* warning emission + content in ``warn_if_in_container``

The module under test uses ``pathlib.Path.exists`` / ``Path.read_text``
(rather than ``os.path.exists`` / ``open``) and reads ``sys.platform``
via a local ``import sys`` inside the function. We therefore patch the
``Path`` class methods and ``sys.platform`` directly (``sys`` is a
singleton module, so the local import sees our patch).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from voice_typer.server import container_detect

_LOGGER_NAME = "voice_typer.server.container_detect"


# ── helpers ────────────────────────────────────────────────────────────


def _force_linux(monkeypatch) -> None:
    """Force the module to behave as if running on Linux."""
    monkeypatch.setattr("sys.platform", "linux")


def _set_existing_paths(monkeypatch, existing: set[str]) -> None:
    """Make ``Path.exists()`` return True only for paths in ``existing``.

    The ``existing`` paths are passed through ``str(Path(p))`` so that
    they are stored in the *runner's* native path representation. The
    module under test constructs ``Path`` instances on the same runner,
    so ``str(self)`` in ``_exists`` yields the same representation and
    the membership test succeeds regardless of platform (e.g. on win32
    ``str(Path("/.dockerenv"))`` is ``"\\\\.dockerenv"`` rather than
    ``"/.dockerenv"``, so a raw comparison against the POSIX literal
    would silently miss every patched path).
    """
    existing_normalized = {str(Path(p)) for p in existing}

    def _exists(self: Path) -> bool:
        return str(self) in existing_normalized

    monkeypatch.setattr(Path, "exists", _exists)


def _set_cgroup(monkeypatch, content: str | None) -> None:
    """Configure ``Path.read_text`` to return ``content`` for ``/proc/1/cgroup``.

    If ``content`` is ``None``, reading ``/proc/1/cgroup`` raises
    ``OSError`` (simulating a missing/unreadable file). Reads of any
    other path raise ``FileNotFoundError`` — the module under test only
    reads ``/proc/1/cgroup`` so this never triggers in practice.
    """

    def _read_text(self: Path, *args, **kwargs) -> str:
        # Compare against ``str(Path("/proc/1/cgroup"))`` rather than the
        # POSIX literal so the match succeeds on every runner: on win32
        # ``str(Path("/proc/1/cgroup"))`` is ``"\\proc\\1\\cgroup"``,
        # which is exactly what ``str(self)`` yields for the SUT's
        # ``Path("/proc/1/cgroup")`` call. (``FileNotFoundError(str(self))``
        # below is just a diagnostic message and needs no normalization.)
        if str(self) == str(Path("/proc/1/cgroup")):
            if content is None:
                raise OSError("cannot read /proc/1/cgroup")
            return content
        raise FileNotFoundError(str(self))

    monkeypatch.setattr(Path, "read_text", _read_text)


def _clear_container_env(monkeypatch) -> None:
    """Ensure ``CONTAINER`` env var is unset."""
    monkeypatch.delenv("CONTAINER", raising=False)


# ── is_in_container ────────────────────────────────────────────────────


class TestIsInContainer:
    """Behavioral tests for :func:`container_detect.is_in_container`."""

    def test_non_linux_returns_false_even_with_dockerenv(self, monkeypatch):
        """On Windows/macOS, ``is_in_container`` is always False."""
        monkeypatch.setattr("sys.platform", "win32")
        _set_existing_paths(monkeypatch, {"/.dockerenv"})
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is False

    def test_linux_no_indicators_returns_false(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "0::/\n")
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is False

    def test_dockerenv_file_detected(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, {"/.dockerenv"})
        _set_cgroup(monkeypatch, "0::/\n")
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is True

    def test_podman_containerenv_file_detected(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, {"/run/.containerenv"})
        _set_cgroup(monkeypatch, "0::/\n")
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is True

    def test_systemd_nspawn_env_var_detected(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "0::/\n")
        monkeypatch.setenv("CONTAINER", "systemd-nspawn")
        assert container_detect.is_in_container() is True

    def test_empty_container_env_var_not_detected(self, monkeypatch):
        """An empty ``CONTAINER`` env var must NOT count as a container.

        ``os.environ.get("CONTAINER")`` returns ``""`` (falsy), which
        the module correctly treats as "not set".
        """
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "0::/\n")
        monkeypatch.setenv("CONTAINER", "")
        assert container_detect.is_in_container() is False

    @pytest.mark.parametrize("sig", ["docker", "lxc", "kubepods", "containerd"])
    def test_cgroup_signature_detected(self, monkeypatch, sig):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, f"0::/{sig}/abc\n")
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is True

    def test_cgroup_unreadable_oserror_returns_false(self, monkeypatch):
        """If ``/proc/1/cgroup`` cannot be read (OSError), fall through to False."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, None)
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is False

    def test_cgroup_unreadable_permission_error_returns_false(self, monkeypatch):
        """``PermissionError`` is a subclass of ``OSError``; the module
        catches both explicitly via ``except (OSError, PermissionError)``."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())

        def _raise(self: Path, *args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "read_text", _raise)
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is False

    def test_cgroup_empty_content_returns_false(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "")
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is False


# ── get_container_type ─────────────────────────────────────────────────


class TestGetContainerType:
    """Behavioral tests for :func:`container_detect.get_container_type`."""

    def test_non_linux_returns_none(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "darwin")
        _set_existing_paths(monkeypatch, {"/.dockerenv"})
        _clear_container_env(monkeypatch)
        assert container_detect.get_container_type() is None

    def test_no_container_returns_none(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "0::/\n")
        _clear_container_env(monkeypatch)
        assert container_detect.get_container_type() is None

    def test_docker(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, {"/.dockerenv"})
        _set_cgroup(monkeypatch, "0::/\n")
        _clear_container_env(monkeypatch)
        assert container_detect.get_container_type() == "docker"

    def test_podman(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, {"/run/.containerenv"})
        _set_cgroup(monkeypatch, "0::/\n")
        _clear_container_env(monkeypatch)
        assert container_detect.get_container_type() == "podman"

    def test_systemd_nspawn_includes_value(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "0::/\n")
        monkeypatch.setenv("CONTAINER", "lxc")
        assert container_detect.get_container_type() == "systemd-nspawn (lxc)"

    def test_kubernetes_via_cgroup(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "0::/kubepods/burstable/pod-abc\n")
        _clear_container_env(monkeypatch)
        assert container_detect.get_container_type() == "kubernetes"

    def test_containerd_via_cgroup(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "0::/system.slice/containerd.service\n")
        _clear_container_env(monkeypatch)
        assert container_detect.get_container_type() == "containerd"

    def test_lxc_via_cgroup(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "0::/lxc/foo\n")
        _clear_container_env(monkeypatch)
        assert container_detect.get_container_type() == "lxc"

    def test_docker_signature_in_cgroup_returns_unknown(self, monkeypatch):
        """KNOWN INCONSISTENCY: ``is_in_container`` detects a 'docker'
        signature in ``/proc/1/cgroup``, but ``get_container_type`` only
        maps 'kubepods', 'containerd', 'lxc' — not 'docker'. So a
        container detected solely via cgroup (no ``/.dockerenv``) is
        reported as 'unknown'. This test pins the current behavior."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "0::/docker/abc123\n")
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is True
        assert container_detect.get_container_type() == "unknown"

    def test_precedence_docker_wins_over_podman_and_env(self, monkeypatch):
        """When multiple indicators are present, the first checked wins
        (Docker > Podman > CONTAINER env > cgroup)."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, {"/.dockerenv", "/run/.containerenv"})
        _set_cgroup(monkeypatch, "0::/kubepods/pod-x\n")
        monkeypatch.setenv("CONTAINER", "lxc")
        assert container_detect.get_container_type() == "docker"

    def test_precedence_podman_wins_over_env(self, monkeypatch):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, {"/run/.containerenv"})
        _set_cgroup(monkeypatch, "0::/\n")
        monkeypatch.setenv("CONTAINER", "systemd-nspawn")
        assert container_detect.get_container_type() == "podman"


# ── warn_if_in_container ───────────────────────────────────────────────


class TestWarnIfInContainer:
    """Behavioral tests for :func:`container_detect.warn_if_in_container`."""

    def test_no_warning_when_not_in_container(self, monkeypatch, caplog):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_cgroup(monkeypatch, "0::/\n")
        _clear_container_env(monkeypatch)
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            container_detect.warn_if_in_container()
        assert not any("PLAT-021" in r.message for r in caplog.records)

    def test_warning_emitted_when_in_docker(self, monkeypatch, caplog):
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, {"/.dockerenv"})
        _set_cgroup(monkeypatch, "0::/\n")
        _clear_container_env(monkeypatch)
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            container_detect.warn_if_in_container()
        plat_records = [r for r in caplog.records if "PLAT-021" in r.message]
        assert len(plat_records) == 1
        # %-formatting happens lazily via getMessage()
        assert "docker" in plat_records[0].getMessage()

    def test_warning_lists_unavailable_features(self, monkeypatch, caplog):
        """The warning must enumerate every degraded feature so users
        understand *why* something doesn't work."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, {"/.dockerenv"})
        _set_cgroup(monkeypatch, "0::/\n")
        _clear_container_env(monkeypatch)
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            container_detect.warn_if_in_container()
        msg = caplog.records[-1].getMessage()
        assert "system tray" in msg
        assert "audio capture" in msg
        assert "GPU" in msg
        assert "global hotkeys" in msg

    def test_warning_not_emitted_on_non_linux(self, monkeypatch, caplog):
        """Even if ``/.dockerenv`` existed on a non-Linux host (e.g. a
        mounted volume), the platform gate suppresses detection."""
        monkeypatch.setattr("sys.platform", "win32")
        _set_existing_paths(monkeypatch, {"/.dockerenv"})
        _clear_container_env(monkeypatch)
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            container_detect.warn_if_in_container()
        assert not any("PLAT-021" in r.message for r in caplog.records)
