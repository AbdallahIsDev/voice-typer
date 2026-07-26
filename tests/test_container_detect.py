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
from pathlib import Path, PurePosixPath

import pytest
from voice_typer.server import container_detect

_LOGGER_NAME = "voice_typer.server.container_detect"


# ── helpers ────────────────────────────────────────────────────────────
#
# TEST-GAP-1 (2026-07-18): Path comparison helpers use ``PurePosixPath``
# for normalization so the tests pass on win32, Linux, and macOS without
# depending on the runner's native ``Path`` string representation. The
# SUT constructs ``Path("/.dockerenv")`` etc.; on win32 the native str
# repr is ``"\\.dockerenv"`` (no drive), on Linux it is ``"/.dockerenv"``.
# Both representations normalize to the same ``PurePosixPath`` string
# (``/.dockerenv``) via ``Path.as_posix()``, so the membership test
# succeeds regardless of runner platform. ``Path.as_posix()`` is the
# documented cross-platform way to get a forward-slash string from any
# ``Path`` (concrete or pure) — it returns the path with ``/`` separators
# on every platform, which makes it a safe key for ``PurePosixPath``.


def _normalize(p) -> str:
    """Normalize a path-like to a POSIX-style string for cross-platform comparison.

    Accepts a ``str``, a concrete ``Path``, or a ``PurePath``. Always
    returns a forward-slash string (e.g. ``/.dockerenv``) regardless of
    the runner platform. This is the single source of truth for path
    comparison in this test module.
    """
    if isinstance(p, str):
        return str(PurePosixPath(p))
    # Concrete Path / PurePath: use as_posix() to force forward slashes.
    return p.as_posix() if hasattr(p, "as_posix") else str(PurePosixPath(str(p)))


def _force_linux(monkeypatch) -> None:
    """Force the module to behave as if running on Linux."""
    monkeypatch.setattr("sys.platform", "linux")


def _force_win32(monkeypatch) -> None:
    """Force the module to behave as if running on Windows."""
    monkeypatch.setattr("sys.platform", "win32")


def _set_existing_paths(monkeypatch, existing: set[str]) -> None:
    """Make ``Path.exists()`` return True only for paths in ``existing``.

    Path comparison is done via the ``_normalize`` helper, which uses
    ``PurePosixPath`` to produce a forward-slash canonical form. This
    is robust against the runner's native path separator (backslash on
    win32, forward slash on Linux/macOS) and against drive-letter
    differences (``Path("/x")`` on win32 has no drive; ``Path("C:/x")``
    would normalize to ``/x`` only if the drive is empty).
    """
    existing_normalized = {_normalize(p) for p in existing}

    def _exists(self: Path) -> bool:
        return _normalize(self) in existing_normalized

    monkeypatch.setattr(Path, "exists", _exists)


def _set_cgroup(monkeypatch, content: str | None) -> None:
    """Configure ``Path.read_text`` to return ``content`` for ``/proc/1/cgroup``.

    If ``content`` is ``None``, reading ``/proc/1/cgroup`` raises
    ``OSError`` (simulating a missing/unreadable file). Reads of any
    other path raise ``FileNotFoundError`` — the module under test only
    reads ``/proc/1/cgroup`` so this never triggers in practice.
    """
    cgroup_posix = _normalize(Path("/proc/1/cgroup"))

    def _read_text(self: Path, *args, **kwargs) -> str:
        # Compare normalized POSIX forms so the match succeeds on every
        # runner (see ``_normalize`` docstring for the cross-platform
        # rationale). ``FileNotFoundError(str(self))`` below is just a
        # diagnostic message and needs no normalization.
        if _normalize(self) == cgroup_posix:
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


# ── DE-66: cgroup v2-aware detection ───────────────────────────────────


def _set_proc_files(monkeypatch, contents: dict[str, str | None]) -> None:
    """Configure ``Path.read_text`` to return per-path content.

    Parameters
    ----------
    contents : dict[str, str | None]
        Maps POSIX-normalized path → file content. If a path maps to
        ``None``, reading it raises ``OSError``. Reading any path NOT
        in the dict raises ``FileNotFoundError`` (caught by the module
        under test's ``except OSError`` in ``_read_proc_file``).

    This helper is the cgroup-v2-aware counterpart of ``_set_cgroup``:
    it can set up multiple ``/proc`` files simultaneously so tests can
    simulate rootless Podman (which writes to ``/proc/1/environ`` and
    ``/proc/self/mountinfo`` but NOT ``/proc/1/cgroup``).
    """
    normalized = {_normalize(Path(p)): v for p, v in contents.items()}

    def _read_text(self: Path, *args, **kwargs) -> str:
        key = _normalize(self)
        if key in normalized:
            value = normalized[key]
            if value is None:
                raise OSError(f"cannot read {self}")
            return value
        raise FileNotFoundError(str(self))

    monkeypatch.setattr(Path, "read_text", _read_text)


class TestCgroupV2Detection:
    """DE-66: cgroup v2-aware detection for rootless Podman and modern
    OCI runtimes.

    On cgroup v2 (default on Linux kernels 5.15+), rootless Podman
    containers do NOT create ``/run/.containerenv`` and may not write a
    recognizable signature into ``/proc/1/cgroup`` (the path is often
    just ``0::/``). Pre-fix, these containers were misdetected as
    "not in container" — causing the app to attempt unavailable
    features (system tray, audio capture, GPU) inside the container,
    producing confusing errors instead of the graceful degradation
    that ``warn_if_in_container`` is meant to provide.

    The fix adds two cgroup v2-aware detection paths:

    1. ``container=`` env var on PID 1 (read via ``/proc/1/environ``)
    2. overlayfs rooted at ``/`` (read via ``/proc/self/mountinfo``)
    """

    def test_proc1_environ_container_var_detected(self, monkeypatch):
        """DE-66: ``container=oci`` (or any value) on PID 1's environ
        must trigger container detection even when cgroup is
        uninformative (``0::/``) and no ``/.dockerenv`` /
        ``/run/.containerenv`` exist."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())  # no .dockerenv / .containerenv
        _set_proc_files(
            monkeypatch,
            {
                "/proc/1/cgroup": "0::/\n",  # uninformative cgroup v2
                "/proc/1/environ": "PATH=/usr/bin\x00container=oci\x00",
                "/proc/self/mountinfo": "",  # no overlayfs
            },
        )
        _clear_container_env(monkeypatch)  # current process env has no CONTAINER
        assert container_detect.is_in_container() is True

    def test_proc1_environ_podman_value_detected(self, monkeypatch):
        """DE-66: ``container=podman`` on PID 1's environ triggers
        detection — rootless Podman commonly sets this."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_proc_files(
            monkeypatch,
            {
                "/proc/1/cgroup": "0::/\n",
                "/proc/1/environ": "container=podman\x00HOME=/root\x00",
                "/proc/self/mountinfo": "",
            },
        )
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is True

    def test_proc1_environ_no_container_var_not_detected(self, monkeypatch):
        """DE-66: PID 1 environ WITHOUT a ``container=`` var must NOT
        trigger detection (the env-var path is specific to the exact
        key ``container``, not a substring match)."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_proc_files(
            monkeypatch,
            {
                "/proc/1/cgroup": "0::/\n",
                # NOTE: ``MY_CONTAINER=foo`` must NOT match (key != "container")
                "/proc/1/environ": "PATH=/bin\x00MY_CONTAINER=foo\x00",
                "/proc/self/mountinfo": "",
            },
        )
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is False

    def test_mountinfo_overlay_at_root_detected(self, monkeypatch):
        """DE-66: an ``overlay`` filesystem mounted at ``/`` in
        ``/proc/self/mountinfo`` must trigger container detection
        (catches rootless Podman and other OCI runtimes)."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        # A representative rootless-Podman mountinfo line: mount point
        # is field 5 (``/``), fstype is the token after the ``-``
        # separator (``overlay``).
        mountinfo = (
            "1 0 0:1 / / rw,relatime - overlay overlay "
            "rw,lowerdir=/var/lib/containers,upperdir=/var/lib/containers/overlay-containers/xyz/usr,diff\n"
            "30 1 0:28 / /proc rw,nosuid,nodev,noexec - proc proc rw\n"
        )
        _set_proc_files(
            monkeypatch,
            {
                "/proc/1/cgroup": "0::/\n",
                "/proc/1/environ": "",
                "/proc/self/mountinfo": mountinfo,
            },
        )
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is True

    def test_mountinfo_overlay_not_at_root_not_detected(self, monkeypatch):
        """DE-66: an ``overlay`` filesystem mounted at a NON-root
        location (e.g. ``/var/lib/docker/overlay2``) must NOT trigger
        container detection — only overlay-at-root is a reliable
        container indicator (host systems may use overlayfs for
        /var/lib/docker or /home)."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        # overlay mounted at /var/lib/docker, not /
        mountinfo = "30 1 0:28 /var/lib/docker /var/lib/docker rw,relatime - overlay overlay rw\n"
        _set_proc_files(
            monkeypatch,
            {
                "/proc/1/cgroup": "0::/\n",
                "/proc/1/environ": "",
                "/proc/self/mountinfo": mountinfo,
            },
        )
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is False

    def test_mountinfo_non_overlay_at_root_not_detected(self, monkeypatch):
        """DE-66: a NON-overlay filesystem (ext4, btrfs) at ``/`` must
        NOT trigger detection — only overlay-at-root is the indicator."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        mountinfo = "1 0 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n"
        _set_proc_files(
            monkeypatch,
            {
                "/proc/1/cgroup": "0::/\n",
                "/proc/1/environ": "",
                "/proc/self/mountinfo": mountinfo,
            },
        )
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is False

    def test_rootless_podman_no_containerenv_detected_via_environ(self, monkeypatch):
        """DE-66: simulated rootless Podman — no ``/run/.containerenv``,
        no cgroup signature, but ``container=podman`` on PID 1 — must
        be detected. This is the exact scenario the fix targets."""
        _force_linux(monkeypatch)
        # No /.dockerenv, no /run/.containerenv
        _set_existing_paths(monkeypatch, set())
        _set_proc_files(
            monkeypatch,
            {
                "/proc/1/cgroup": "0::/\n",  # no signature
                "/proc/1/environ": "container=podman\x00HOME=/root\x00PATH=/usr/bin\n",
                "/proc/self/mountinfo": "1 0 0:1 / / rw - overlay overlay rw\n",
            },
        )
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is True

    def test_rootless_podman_no_environ_var_detected_via_mountinfo(self, monkeypatch):
        """DE-66: if the rootless Podman runtime does NOT set
        ``container=``, the overlayfs-at-root fallback must still
        detect the container."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_proc_files(
            monkeypatch,
            {
                "/proc/1/cgroup": "0::/\n",
                "/proc/1/environ": "PATH=/usr/bin\n",  # no container= var
                "/proc/self/mountinfo": "1 0 0:1 / / rw - overlay overlay rw\n",
            },
        )
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is True

    def test_get_container_type_returns_runtime_value_from_environ(self, monkeypatch):
        """DE-66: ``get_container_type`` must return the ``container=``
        value (e.g. ``oci``, ``podman``, ``lxc``) when the env-var path
        fires — instead of generic ``unknown``."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_proc_files(
            monkeypatch,
            {
                "/proc/1/cgroup": "0::/\n",
                "/proc/1/environ": "container=oci\x00",
                "/proc/self/mountinfo": "",
            },
        )
        _clear_container_env(monkeypatch)
        assert container_detect.get_container_type() == "oci"

    def test_get_container_type_overlay_fallback_label(self, monkeypatch):
        """DE-66: when ONLY the overlayfs-at-root indicator fires
        (no env-var, no cgroup signature, no .dockerenv), the
        container type must be a recognizable label (not bare
        ``unknown``) so operators can diagnose."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_proc_files(
            monkeypatch,
            {
                "/proc/1/cgroup": "0::/\n",
                "/proc/1/environ": "",
                "/proc/self/mountinfo": "1 0 0:1 / / rw - overlay overlay rw\n",
            },
        )
        _clear_container_env(monkeypatch)
        ct = container_detect.get_container_type()
        assert ct is not None
        # Must mention overlayfs so the operator knows which indicator fired.
        assert "overlay" in ct.lower()

    def test_proc1_environ_unreadable_does_not_break_detection(self, monkeypatch):
        """DE-66: if ``/proc/1/environ`` is unreadable (OSError /
        PermissionError — common in hardened containers where PID 1
        is owned by another user), detection must fall through to
        the next indicator (overlayfs) rather than raising."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_proc_files(
            monkeypatch,
            {
                "/proc/1/cgroup": "0::/\n",
                "/proc/1/environ": None,  # OSError on read
                "/proc/self/mountinfo": "1 0 0:1 / / rw - overlay overlay rw\n",
            },
        )
        _clear_container_env(monkeypatch)
        # Must not raise; must detect via overlay fallback.
        assert container_detect.is_in_container() is True

    def test_mountinfo_unreadable_does_not_break_detection(self, monkeypatch):
        """DE-66: if ``/proc/self/mountinfo`` is unreadable, detection
        must fall through without raising."""
        _force_linux(monkeypatch)
        _set_existing_paths(monkeypatch, set())
        _set_proc_files(
            monkeypatch,
            {
                "/proc/1/cgroup": "0::/\n",
                "/proc/1/environ": "container=oci\x00",  # detected via environ
                "/proc/self/mountinfo": None,  # OSError on read
            },
        )
        _clear_container_env(monkeypatch)
        assert container_detect.is_in_container() is True
