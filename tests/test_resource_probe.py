"""Unit tests for ``voice_typer.server.resource_probe``.

This file is the dedicated test suite for the extracted pre-flight
RAM/disk/GPU probe (WN-20 — Phase 4.5 spaghetti split). The original
``DictationPipeline._check_resources`` tests in
``tests/test_dictation_pipeline_check_resources.py`` still pass via the
1-line delegator (they exercise the method on the class); these tests
exercise the free function directly so the probe can be unit-tested in
isolation, without instantiating a ``DictationPipeline``.

All externals are mocked:
- ``psutil.virtual_memory`` — RAM probe
- ``shutil.disk_usage`` — Windows disk probe (when ``os.statvfs`` is absent)
- ``os.statvfs`` — POSIX disk probe (Linux / macOS)
- ``torch.cuda.*`` — GPU memory probe
- ``ctypes.windll`` — Windows RAM fallback (when ``psutil`` is unavailable)

AGENTS.md C-DATA-1: the probe performs NO network calls — all
mocks here are for in-process local-system probes.
"""

from __future__ import annotations

import inspect
import logging
import sys

from voice_typer.server import resource_probe
from voice_typer.server.resource_probe import (
    DEFAULT_CHECK_INTERVAL,
    check_resources,
    check_resources_throttled,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _fake_vm(available_bytes: int) -> object:
    """Fake ``psutil.virtual_memory`` return value with ``.available``."""
    return type("_FakeVM", (), {"available": available_bytes})()


def _fake_disk_usage(free_bytes: int) -> object:
    """Fake ``shutil.disk_usage`` return value with ``.free``."""
    return type("_FakeDiskUsage", (), {"free": free_bytes})()


def _fake_statvfs(free_bytes: int) -> object:
    """Fake ``os.statvfs`` return value.

    The probe computes ``free_gb = (statvfs.f_bavail * statvfs.f_frsize) / 1024**3``.
    We pick ``f_frsize = 1`` so ``f_bavail`` equals the byte count, which
    makes the math easy to reason about in the test.
    """
    return type(
        "_FakeStatVfs",
        (),
        {"f_bavail": free_bytes, "f_frsize": 1},
    )()


def _fake_device_props(total_memory_bytes: int) -> object:
    """Fake ``torch.cuda.get_device_properties(0)`` with ``.total_memory``."""
    return type(
        "_FakeDeviceProps",
        (),
        {"total_memory": total_memory_bytes},
    )()


def _patch_psutil_unavailable(monkeypatch) -> None:
    """Make ``psutil`` appear unimportable.

    Removes ``psutil`` from ``sys.modules`` and patches
    ``builtins.__import__`` to raise ``ImportError`` for ``psutil``.
    The real ``__import__`` is saved first so the wrapper can delegate
    non-psutil imports without recursing.
    """
    monkeypatch.delitem(sys.modules, "psutil", raising=False)

    import builtins as _builtins_mod

    _real_import = _builtins_mod.__import__

    def _mock_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError(f"No module named '{name}'")
        return _real_import(name, *args, **kwargs)

    monkeypatch.setattr(_builtins_mod, "__import__", _mock_import)


# ── RAM check ─────────────────────────────────────────────────────────────


class TestCheckResourcesRAM:
    """check_resources: RAM health-check paths."""

    def test_logs_ram_info_when_sufficient(self, caplog, monkeypatch):
        """When psutil reports > 2048 MB available, an INFO line shows
        the amount and no warning or moderate-RAM info is emitted."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),  # 4 GB available
        )
        # Avoid the POSIX disk branch actually logging disk info that
        # could interfere with assertions on RAM-only records. Patch
        # statvfs to return plenty of free space.
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.resource_probe"):
            check_resources()

        info_lines = [r for r in caplog.records if "[RESOURCE] Available RAM" in r.getMessage()]
        assert info_lines, "Should log available RAM when psutil is available"

        low_ram_warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Low RAM" in r.getMessage()]
        assert not low_ram_warnings, "Should NOT warn about low RAM when > 2048 MB is available"

        moderate_lines = [r for r in caplog.records if "RAM is moderate" in r.getMessage()]
        assert not moderate_lines, "Should NOT log moderate RAM when > 2048 MB is available"

    def test_warns_when_ram_below_1024_mb(self, caplog, monkeypatch):
        """When available RAM < 1024 MB, a WARNING about heap corruption
        is logged with the 0xC0000374 code."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(512 * 1024**2),  # 512 MB available
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.resource_probe"):
            check_resources()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Low RAM" in r.getMessage()]
        assert warnings, "Should log WARNING when RAM < 1024 MB"
        assert "0xC0000374" in warnings[0].getMessage(), "Warning must mention heap corruption exit code (0xC0000374)"

    def test_infos_moderate_ram_between_1024_and_2048_mb(self, caplog, monkeypatch):
        """When available RAM is 1024-2048 MB, an INFO line about
        moderate RAM is logged (not an error — the user can still
        transcribe with smaller models)."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(1500 * 1024**2),  # ~1.5 GB available
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.resource_probe"):
            check_resources()

        moderate_lines = [r for r in caplog.records if "RAM is moderate" in r.getMessage()]
        assert moderate_lines, "Should log INFO about moderate RAM when available is 1024-2048 MB"


# ── Disk check ────────────────────────────────────────────────────────────


class TestCheckResourcesDisk:
    """check_resources: disk-space check paths.

    The probe uses ``os.statvfs`` on POSIX (Linux/macOS) and falls back
    to ``shutil.disk_usage`` on Windows (when ``os.statvfs`` is absent).
    Both branches are exercised here.
    """

    def test_logs_disk_info_when_sufficient_posix(self, caplog, monkeypatch):
        """POSIX path: when ``os.statvfs`` reports > 1 GB free, an INFO
        line shows the free space and no warning is emitted."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.resource_probe"):
            check_resources()

        disk_lines = [r for r in caplog.records if "[RESOURCE] Disk free" in r.getMessage()]
        assert disk_lines, "Should log disk free space via os.statvfs on POSIX"

        disk_warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "Critically low disk" in r.getMessage()
        ]
        assert not disk_warnings, "Should NOT warn about low disk when > 1 GB is free"

    def test_warns_when_disk_below_1_gb_posix(self, caplog, monkeypatch):
        """POSIX path: when ``os.statvfs`` reports < 1 GB free, a
        WARNING about heap-corruption risk is logged."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(500 * 1024**2), raising=False)  # 500 MB free

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.resource_probe"):
            check_resources()

        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "Critically low disk" in r.getMessage()
        ]
        assert warnings, "Should log WARNING when statvfs reports < 1 GB free"

    def test_logs_disk_info_via_shutil_when_statvfs_unavailable(self, caplog, monkeypatch):
        """Windows path: when ``os.statvfs`` is absent, the probe falls
        back to ``shutil.disk_usage``. Verify the INFO line is emitted
        with the drive path."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        # Make ``os.statvfs`` appear absent (Windows-like) by deleting
        # the attribute. ``hasattr(os, "statvfs")`` then returns False
        # and the shutil.disk_usage branch runs.
        monkeypatch.delattr("os.statvfs", raising=False)
        monkeypatch.setattr("shutil.disk_usage", lambda path: _fake_disk_usage(50 * 1024**3))

        with caplog.at_level(logging.INFO, logger="voice_typer.server.resource_probe"):
            check_resources()

        # On the Windows branch the log line is "Disk free on %s: %.1f GB"
        # (includes the path); on POSIX it's "Disk free: %.1f GB". Match
        # the Windows-shaped line.
        disk_lines = [r for r in caplog.records if "[RESOURCE] Disk free on" in r.getMessage()]
        assert disk_lines, "Should log disk free space via shutil.disk_usage when os.statvfs is absent"

    def test_warns_when_disk_below_1_gb_via_shutil(self, caplog, monkeypatch):
        """Windows path: when ``shutil.disk_usage`` reports < 1 GB free
        on a monitored drive, a WARNING is logged."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.delattr("os.statvfs", raising=False)
        monkeypatch.setattr("shutil.disk_usage", lambda path: _fake_disk_usage(500 * 1024**2))

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.resource_probe"):
            check_resources()

        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "Critically low disk" in r.getMessage()
        ]
        assert warnings, "Should log WARNING when shutil.disk_usage reports < 1 GB free on Windows path"

    def test_handles_disk_usage_failure_gracefully(self, caplog, monkeypatch):
        """When ``shutil.disk_usage`` raises on every drive (Windows
        path), the probe skips that drive and still completes."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.delattr("os.statvfs", raising=False)

        def _failing_disk_usage(path):
            raise PermissionError(f"Cannot access {path}")

        monkeypatch.setattr("shutil.disk_usage", _failing_disk_usage)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.resource_probe"):
            check_resources()

        complete_lines = [r for r in caplog.records if "[RESOURCE] Pre-flight health check complete" in r.getMessage()]
        assert complete_lines, (
            "check_resources must complete without crashing even when shutil.disk_usage raises on all drives"
        )


# ── GPU check ─────────────────────────────────────────────────────────────


class TestCheckResourcesGPU:
    """check_resources: GPU memory-check paths.

    The ``mock_heavy_imports`` autouse fixture in ``tests/conftest.py``
    installs a mock ``torch`` in ``sys.modules``. These tests pin
    specific return values on that mock to exercise the GPU-check
    branches.
    """

    def test_logs_gpu_info_when_sufficient(self, caplog, monkeypatch):
        """When ``torch.cuda`` is available and GPU memory is sufficient
        (> 512 MB free), an INFO line shows allocated / reserved / free."""
        total_memory = 8 * 1024**3  # 8 GB total
        allocated = 2 * 1024**3  # 2 GB allocated → 6144 MB free > 512 MB

        monkeypatch.setattr("torch.cuda.is_available", lambda: True)
        monkeypatch.setattr("torch.cuda.memory_allocated", lambda: allocated)
        monkeypatch.setattr("torch.cuda.memory_reserved", lambda: allocated)
        monkeypatch.setattr(
            "torch.cuda.get_device_properties",
            lambda device: _fake_device_props(total_memory),
        )
        # Avoid noisy disk/RAM records in this test's caplog.
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.resource_probe"):
            check_resources()

        gpu_lines = [r for r in caplog.records if "[RESOURCE] GPU memory" in r.getMessage()]
        assert gpu_lines, "Should log GPU memory info when torch.cuda is available"

        gpu_warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "Low GPU memory" in r.getMessage()
        ]
        assert not gpu_warnings, "Should NOT warn when GPU has > 512 MB free"

    def test_warns_when_gpu_memory_below_512_mb(self, caplog, monkeypatch):
        """When ``torch.cuda`` reports < 512 MB free GPU memory, a
        WARNING about CUDA out-of-memory errors is logged."""
        total_memory = 1024**3  # 1 GB total
        allocated = 900 * 1024**2  # 900 MB allocated → 124 MB free < 512 MB

        monkeypatch.setattr("torch.cuda.is_available", lambda: True)
        monkeypatch.setattr("torch.cuda.memory_allocated", lambda: allocated)
        monkeypatch.setattr("torch.cuda.memory_reserved", lambda: allocated)
        monkeypatch.setattr(
            "torch.cuda.get_device_properties",
            lambda device: _fake_device_props(total_memory),
        )
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.resource_probe"):
            check_resources()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Low GPU memory" in r.getMessage()]
        assert warnings, "Should log WARNING when free GPU memory < 512 MB"


# ── Logger parameter ──────────────────────────────────────────────────────


class TestCheckResourcesLogger:
    """check_resources: the ``logger`` parameter routes records to either
    the module logger (default) or a caller-supplied logger (used by the
    DictationPipeline delegator to preserve the historical logger name)."""

    def test_logs_under_module_logger_by_default(self, caplog, monkeypatch):
        """With no ``logger`` argument, records appear under
        ``voice_typer.server.resource_probe``."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.resource_probe"):
            check_resources()

        ram_lines = [r for r in caplog.records if "[RESOURCE] Available RAM" in r.getMessage()]
        assert ram_lines, "Default logger should emit under voice_typer.server.resource_probe"
        assert ram_lines[0].name == "voice_typer.server.resource_probe"

    def test_logs_under_passed_logger_when_provided(self, caplog, monkeypatch):
        """When ``logger=`` is passed (as the DictationPipeline delegator
        does), records appear under that logger's name."""
        custom_logger = logging.getLogger("voice_typer.server.dictation_pipeline")
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            check_resources(logger=custom_logger)

        ram_lines = [r for r in caplog.records if "[RESOURCE] Available RAM" in r.getMessage()]
        assert ram_lines, "Passed logger should emit the RAM INFO line"
        assert ram_lines[0].name == "voice_typer.server.dictation_pipeline", (
            "Records must route to the caller-supplied logger (preserves the "
            "historical logger name when called via DictationPipeline delegator)"
        )


# ── Graceful degradation ──────────────────────────────────────────────────


class TestCheckResourcesGracefulDegradation:
    """check_resources must never raise — every sub-check is wrapped in
    try/except. Even when all dependencies fail, the function completes
    and logs a final ``complete`` line."""

    def test_completes_without_crash_when_psutil_unavailable(self, caplog, monkeypatch):
        """When psutil is not importable and the ctypes fallback also
        doesn't apply (non-Windows), the RAM check logs DEBUG and the
        function continues to the disk check."""
        _patch_psutil_unavailable(monkeypatch)
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.resource_probe"):
            check_resources()

        complete_lines = [r for r in caplog.records if "[RESOURCE] Pre-flight health check complete" in r.getMessage()]
        assert complete_lines, "check_resources must complete without crashing even when psutil is unavailable"

    def test_completes_without_crash_when_all_checks_fail(self, caplog, monkeypatch):
        """When every sub-check fails (psutil unavailable, statvfs raises,
        torch import fails), the function still completes."""
        _patch_psutil_unavailable(monkeypatch)

        # Make statvfs raise on every path AND delattr it on the second
        # pass (the shutil.disk_usage branch). Easiest: delattr it so
        # ``hasattr`` returns False, then make shutil.disk_usage raise.
        monkeypatch.delattr("os.statvfs", raising=False)

        def _failing_disk_usage(path):
            raise OSError(f"Cannot stat {path}")

        monkeypatch.setattr("shutil.disk_usage", _failing_disk_usage)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.resource_probe"):
            check_resources()

        complete_lines = [r for r in caplog.records if "[RESOURCE] Pre-flight health check complete" in r.getMessage()]
        assert complete_lines, "check_resources must complete without crashing even when ALL sub-checks fail"

    def test_ram_ctypes_fallback_failure_logs_debug(self, caplog, monkeypatch):
        """When psutil is unavailable AND the ctypes fallback raises
        (Windows-only code path), a DEBUG line is emitted (not silent
        ``pass``). We exercise the inner ``except Exception`` branch by
        patching ``ctypes.windll`` to raise on attribute access."""
        _patch_psutil_unavailable(monkeypatch)

        # Bypass the ``if os.name == "nt":`` guard by patching ``os.name``
        # to "nt" — the probe reads ``os.name`` at call time.
        monkeypatch.setattr("os.name", "nt")

        # Patch ``ctypes.windll`` to raise AttributeError when accessed.
        # Production code does
        # ``ctypes.windll.kernel32.GlobalMemoryStatusEx(...)``, so an
        # AttributeError on ``windll`` propagates into the surrounding
        # ``except Exception`` block.
        import ctypes as _ctypes_mod

        class _RaisingWindll:
            def __getattr__(self, name):
                raise AttributeError(f"no attribute {name!r}")

        monkeypatch.setattr(_ctypes_mod, "windll", _RaisingWindll(), raising=False)

        # Avoid pathlib INTERNALERROR on non-Windows hosts when
        # ``os.name`` is patched to "nt" (pathlib picks WindowsPath
        # lazily). The probe uses ``pathlib.Path.home()`` in the disk
        # branch — stub it to a no-op class for the duration of this test.
        import pathlib as _pathlib_mod

        class _StubPath:
            def __init__(self, *args, **kwargs):
                pass

            @classmethod
            def home(cls):
                return cls()

            def resolve(self):
                return self

            def __truediv__(self, other):
                return self

            def __fspath__(self):
                return "/stub"

        monkeypatch.setattr(_pathlib_mod, "Path", _StubPath)
        monkeypatch.setattr(_pathlib_mod, "PosixPath", _StubPath, raising=False)
        monkeypatch.setattr(_pathlib_mod, "WindowsPath", _StubPath, raising=False)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.resource_probe"):
            check_resources()

        ctypes_debug_lines = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "RAM check (ctypes fallback) failed" in r.getMessage()
        ]
        assert ctypes_debug_lines, (
            "ctypes-fallback except block must emit "
            "`log.debug('[RESOURCE] RAM check (ctypes fallback) failed (non-fatal)', exc_info=True)`"
        )

    def test_gpu_check_failure_logs_debug(self, caplog, monkeypatch):
        """When torch import succeeds but ``cuda.is_available`` raises,
        a DEBUG line is emitted (not silent ``pass``)."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        def _raising_is_available():
            raise RuntimeError("CUDA driver mismatch")

        monkeypatch.setattr("torch.cuda.is_available", _raising_is_available)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.resource_probe"):
            check_resources()

        gpu_debug_lines = [
            r for r in caplog.records if r.levelno == logging.DEBUG and "GPU check failed" in r.getMessage()
        ]
        assert gpu_debug_lines, (
            "GPU-check except block must emit `log.debug('[RESOURCE] GPU check failed (non-fatal)', exc_info=True)`"
        )

    def test_no_silent_except_pass_in_check_resources_source(self):
        """Static check: ``check_resources`` source must not contain a
        bare ``except Exception: pass`` (the XZ-EH-008 pattern). This
        pins the fix against regression — the original docstring promised
        DEBUG-level failure logging but the code did ``pass``; we must
        not regress to that state."""
        src = inspect.getsource(check_resources)
        lines = src.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "except Exception:":
                # Find the next non-blank line — it must NOT be ``pass``.
                for j in range(i + 1, min(i + 4, len(lines))):
                    body = lines[j].strip()
                    if not body or body.startswith("#"):
                        continue
                    assert body != "pass", (
                        f"Regression: bare `except Exception: pass` re-introduced at "
                        f"check_resources source line ~{i + 1}. Replace with "
                        f"`log.debug(..., exc_info=True)`."
                    )
                    break
            elif stripped.startswith("except Exception:") and stripped.endswith("pass"):
                raise AssertionError(
                    f"Regression: inline `except Exception: pass` at check_resources source line ~{i + 1}."
                )

    def test_docstring_promises_debug_logging(self):
        """The docstring must still promise DEBUG-level failure logging
        (pins the docstring against drift — the prior drift was the
        docstring claiming DEBUG while the code did ``pass``)."""
        doc = check_resources.__doc__ or ""
        assert "DEBUG" in doc, "Regression: check_resources docstring must mention 'DEBUG' level for failure logging."


# ── Throttle wrapper ──────────────────────────────────────────────────────


class TestCheckResourcesThrottled:
    """check_resources_throttled: limits the real check to once per
    ``interval`` seconds. The throttle state is passed in by the caller
    (not held as module-level mutable state) so the function stays pure
    with respect to its throttle inputs."""

    def test_throttle_skips_check_when_recently_run(self, caplog, monkeypatch):
        """When the last check was less than ``interval`` seconds ago,
        the throttle returns the unchanged timestamp and does NOT call
        check_resources."""
        # Patch psutil so we can detect whether the real check ran.
        call_count = {"n": 0}

        def _counting_vm():
            call_count["n"] += 1
            return _fake_vm(4 * 1024**3)

        monkeypatch.setattr("psutil.virtual_memory", _counting_vm)
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        # last_check_ts = 100.0, now = 110.0, interval = 60.0
        # → 110 - 100 = 10 < 60 → SKIP
        result = check_resources_throttled(
            last_check_ts=100.0,
            interval=60.0,
            now=110.0,
        )

        assert result == 100.0, (
            "Throttle should return the unchanged last_check_ts when the interval has not elapsed (no real check ran)"
        )
        assert call_count["n"] == 0, (
            f"psutil.virtual_memory should NOT be called when the throttle skips (call_count={call_count['n']})"
        )

    def test_throttle_runs_check_when_interval_elapsed(self, caplog, monkeypatch):
        """When the last check was long enough ago (> ``interval``
        seconds), the throttle calls check_resources and returns the new
        timestamp."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        # last_check_ts = 0.0, now = 100.0, interval = 60.0
        # → 100 - 0 = 100 ≥ 60 → RUN, return now=100.0
        result = check_resources_throttled(
            last_check_ts=0.0,
            interval=60.0,
            now=100.0,
        )

        assert result == 100.0, "Throttle should return the new timestamp (== now) when the interval has elapsed"

    def test_throttle_uses_real_monotonic_when_now_is_none(self, monkeypatch):
        """When ``now`` is None (the production path), the throttle uses
        ``time.monotonic()``. We patch the resource_probe module's
        ``time.monotonic`` to return a known value."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)
        monkeypatch.setattr(resource_probe.time, "monotonic", lambda: 9999.0)

        # last_check_ts = 0.0, default interval = 60.0
        # → 9999 - 0 = 9999 ≥ 60 → RUN, return 9999.0
        result = check_resources_throttled(last_check_ts=0.0)

        assert result == 9999.0, "Throttle should fall back to time.monotonic() when now=None and return that value"

    def test_throttle_default_interval_is_60_seconds(self):
        """DEFAULT_CHECK_INTERVAL constant pins the documented 60s
        default. Existing tests and DictationPipeline rely on this
        value for the "throttle to once per 60s" contract."""
        assert DEFAULT_CHECK_INTERVAL == 60.0, (
            "DEFAULT_CHECK_INTERVAL must be 60.0 — DictationPipeline.__init__ "
            "uses this as the default for self._resources_check_interval."
        )

    def test_throttle_at_exact_interval_boundary_runs_check(self, monkeypatch):
        """When ``now - last_check_ts == interval`` (exactly at the
        boundary), the check should run (the comparison is ``<``, so the
        boundary is inclusive of the interval)."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        # last_check_ts = 0.0, now = 60.0, interval = 60.0
        # → 60 - 0 = 60, NOT < 60 → RUN
        result = check_resources_throttled(
            last_check_ts=0.0,
            interval=60.0,
            now=60.0,
        )

        assert result == 60.0, (
            "At the exact interval boundary (now - last == interval), the check should run "
            "and return the new timestamp (the comparison is strict <, so the boundary is past the skip window)"
        )

    def test_throttle_forwards_logger_to_check_resources(self, caplog, monkeypatch):
        """When a ``logger=`` is passed, it is forwarded to
        check_resources so records appear under that logger's name
        (DictationPipeline delegator relies on this to preserve the
        historical logger name)."""
        custom_logger = logging.getLogger("voice_typer.server.dictation_pipeline")
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            check_resources_throttled(
                last_check_ts=0.0,
                interval=60.0,
                now=100.0,
                logger=custom_logger,
            )

        ram_lines = [r for r in caplog.records if "[RESOURCE] Available RAM" in r.getMessage()]
        assert ram_lines, (
            "Throttle must forward the logger to check_resources so records appear "
            "under the caller-supplied logger name"
        )
        assert ram_lines[0].name == "voice_typer.server.dictation_pipeline"


# ── Module surface ────────────────────────────────────────────────────────


class TestResourceProbeModuleSurface:
    """Pin the public API of the resource_probe module so accidental
    renames are caught early."""

    def test_module_exposes_check_resources(self):
        assert hasattr(resource_probe, "check_resources"), "resource_probe module must expose check_resources()"
        assert callable(resource_probe.check_resources)

    def test_module_exposes_check_resources_throttled(self):
        assert hasattr(resource_probe, "check_resources_throttled"), (
            "resource_probe module must expose check_resources_throttled()"
        )
        assert callable(resource_probe.check_resources_throttled)

    def test_module_exposes_default_check_interval(self):
        assert hasattr(resource_probe, "DEFAULT_CHECK_INTERVAL"), (
            "resource_probe module must expose DEFAULT_CHECK_INTERVAL constant"
        )

    def test_module_does_not_import_network_libs(self):
        """C-DATA-1: the probe performs NO network calls. The module
        source must not import socket, urllib, http, requests, or any
        other network library."""
        src = inspect.getsource(resource_probe)
        # Crude but effective: scan import statements for network libs.
        # Allow torch / psutil / ctypes / shutil / os / pathlib / time / logging.
        forbidden = ("socket", "urllib", "http.client", "requests", "aiohttp", "httpx")
        for lib in forbidden:
            assert f"import {lib}" not in src, (
                f"C-DATA-1 violation: resource_probe.py must not import network library '{lib}'"
            )
            assert f"from {lib}" not in src, (
                f"C-DATA-1 violation: resource_probe.py must not import from network library '{lib}'"
            )
