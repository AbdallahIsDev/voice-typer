"""
Unit tests for ``voice_typer.server.resource_probe``.
AGENTS.md C-DATA-1: the probe performs NO network calls, all
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


def _fake_vm(available_bytes: int) -> object:
    """Fake ``psutil.virtual_memory`` return value with ``.available``."""
    return type("_FakeVM", (), {"available": available_bytes})()


def _fake_disk_usage(free_bytes: int) -> object:
    """Fake ``shutil.disk_usage`` return value with ``.free``."""
    return type("_FakeDiskUsage", (), {"free": free_bytes})()


def _fake_statvfs(free_bytes: int) -> object:
    """Fake ``os.statvfs`` return value."""
    return type(
        "_FakeStatVfs",
        (),
        {"f_bavail": free_bytes, "f_frsize": 1},
    )()


def _patch_psutil_unavailable(monkeypatch) -> None:
    """Make ``psutil`` appear unimportable."""
    monkeypatch.delitem(sys.modules, "psutil", raising=False)

    import builtins as _builtins_mod

    _real_import = _builtins_mod.__import__

    def _mock_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError(f"No module named '{name}'")
        return _real_import(name, *args, **kwargs)

    monkeypatch.setattr(_builtins_mod, "__import__", _mock_import)


class TestCheckResourcesRAM:
    """check_resources: RAM health-check paths."""

    def test_logs_ram_info_when_sufficient(self, caplog, monkeypatch):
        """When psutil reports > 2048 MB available, an INFO line shows"""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),  # 4 GB available
        )
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
        """When available RAM < 1024 MB, a WARNING about heap corruption"""
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
        """When available RAM is 1024-2048 MB, an INFO line about"""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(1500 * 1024**2),  # ~1.5 GB available
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.resource_probe"):
            check_resources()

        moderate_lines = [r for r in caplog.records if "RAM is moderate" in r.getMessage()]
        assert moderate_lines, "Should log INFO about moderate RAM when available is 1024-2048 MB"


class TestCheckResourcesDisk:
    """check_resources: disk-space check paths."""

    def test_logs_disk_info_when_sufficient_posix(self, caplog, monkeypatch):
        """POSIX path: when ``os.statvfs`` reports > 1 GB free, an INFO"""
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
        """POSIX path: when ``os.statvfs`` reports < 1 GB free, a"""
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
        """Windows path: when ``os.statvfs`` is absent, the probe falls"""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        # Make ``os.statvfs`` appear absent (Windows-like) by deleting
        monkeypatch.delattr("os.statvfs", raising=False)
        monkeypatch.setattr("shutil.disk_usage", lambda path: _fake_disk_usage(50 * 1024**3))

        with caplog.at_level(logging.INFO, logger="voice_typer.server.resource_probe"):
            check_resources()

        # On the Windows branch the log line is "Disk free on %s: %.1f GB"
        disk_lines = [r for r in caplog.records if "[RESOURCE] Disk free on" in r.getMessage()]
        assert disk_lines, "Should log disk free space via shutil.disk_usage when os.statvfs is absent"

    def test_warns_when_disk_below_1_gb_via_shutil(self, caplog, monkeypatch):
        """Windows path: when ``shutil.disk_usage`` reports < 1 GB free"""
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
        """When ``shutil.disk_usage`` raises on every drive (Windows"""
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


class TestCheckResourcesGPU:
    """check_resources: GPU memory-check paths."""

    def test_logs_gpu_info_when_sufficient(self, caplog, monkeypatch):
        """When the nvidia-smi/pynvml probe reports sufficient GPU memory"""
        total_memory = 8 * 1024**3  # 8 GB total
        allocated = 2 * 1024**3  # 2 GB allocated → 6144 MB free > 512 MB

        # Phase 1c (PLAN_ONNX_INTEGRATION.md §6.4): the GPU probe no
        monkeypatch.setattr(
            "voice_typer.server.resource_probe._probe_gpu_memory_via_nvidia_smi",
            lambda: (total_memory // 1024**2, (total_memory - allocated) // 1024**2),
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
        """When the nvidia-smi/pynvml probe reports < 512 MB free GPU"""
        total_memory = 1024**3  # 1 GB total
        allocated = 900 * 1024**2  # 900 MB allocated → 124 MB free < 512 MB

        # Phase 1c (PLAN_ONNX_INTEGRATION.md §6.4): the GPU probe no
        monkeypatch.setattr(
            "voice_typer.server.resource_probe._probe_gpu_memory_via_nvidia_smi",
            lambda: (total_memory // 1024**2, (total_memory - allocated) // 1024**2),
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


class TestCheckResourcesLogger:
    """check_resources: the ``logger`` parameter routes records to either"""

    def test_logs_under_module_logger_by_default(self, caplog, monkeypatch):
        """With no ``logger`` argument, records appear under"""
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
        """When ``logger=`` is passed (as the DictationPipeline delegator"""
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


class TestCheckResourcesGracefulDegradation:
    """try/except. Even when all dependencies fail, the function completes"""

    def test_completes_without_crash_when_psutil_unavailable(self, caplog, monkeypatch):
        """When psutil is not importable and the ctypes fallback also"""
        _patch_psutil_unavailable(monkeypatch)
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.resource_probe"):
            check_resources()

        complete_lines = [r for r in caplog.records if "[RESOURCE] Pre-flight health check complete" in r.getMessage()]
        assert complete_lines, "check_resources must complete without crashing even when psutil is unavailable"

    def test_completes_without_crash_when_all_checks_fail(self, caplog, monkeypatch):
        """When every sub-check fails (psutil unavailable, statvfs raises,"""
        _patch_psutil_unavailable(monkeypatch)

        # Make statvfs raise on every path AND delattr it on the second
        monkeypatch.delattr("os.statvfs", raising=False)

        def _failing_disk_usage(path):
            raise OSError(f"Cannot stat {path}")

        monkeypatch.setattr("shutil.disk_usage", _failing_disk_usage)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.resource_probe"):
            check_resources()

        complete_lines = [r for r in caplog.records if "[RESOURCE] Pre-flight health check complete" in r.getMessage()]
        assert complete_lines, "check_resources must complete without crashing even when ALL sub-checks fail"

    def test_ram_ctypes_fallback_failure_logs_debug(self, caplog, monkeypatch):
        """When psutil is unavailable AND the ctypes fallback raises"""
        _patch_psutil_unavailable(monkeypatch)

        # Bypass the ``if os.name == "nt":`` guard by patching ``os.name``
        monkeypatch.setattr("os.name", "nt")

        # Patch ``ctypes.windll`` to raise AttributeError when accessed.
        import ctypes as _ctypes_mod

        class _RaisingWindll:
            def __getattr__(self, name):
                raise AttributeError(f"no attribute {name!r}")

        monkeypatch.setattr(_ctypes_mod, "windll", _RaisingWindll(), raising=False)

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
        """When torch import succeeds but ``cuda.is_available`` raises,"""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        def _raising_probe():
            raise RuntimeError("CUDA driver mismatch")

        monkeypatch.setattr(
            "voice_typer.server.resource_probe._probe_gpu_memory_via_nvidia_smi",
            _raising_probe,
        )

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.resource_probe"):
            check_resources()

        gpu_debug_lines = [
            r for r in caplog.records if r.levelno == logging.DEBUG and "GPU check failed" in r.getMessage()
        ]
        assert gpu_debug_lines, (
            "GPU-check except block must emit `log.debug('[RESOURCE] GPU check failed (non-fatal)', exc_info=True)`"
        )

    def test_no_silent_except_pass_in_check_resources_source(self):
        """bare ``except Exception: pass`` (the XZ-EH-008 pattern). This"""
        src = inspect.getsource(check_resources)
        lines = src.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "except Exception:":
                # Find the next non-blank line, it must NOT be ``pass``.
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
        """The docstring must still promise DEBUG-level failure logging"""
        doc = check_resources.__doc__ or ""
        assert "DEBUG" in doc, "Regression: check_resources docstring must mention 'DEBUG' level for failure logging."


class TestCheckResourcesThrottled:
    """check_resources_throttled: limits the real check to once per"""

    def test_throttle_skips_check_when_recently_run(self, caplog, monkeypatch):
        """When the last check was less than ``interval`` seconds ago,"""
        # Patch psutil so we can detect whether the real check ran.
        call_count = {"n": 0}

        def _counting_vm():
            call_count["n"] += 1
            return _fake_vm(4 * 1024**3)

        monkeypatch.setattr("psutil.virtual_memory", _counting_vm)
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

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
        """When the last check was long enough ago (> ``interval``"""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

        result = check_resources_throttled(
            last_check_ts=0.0,
            interval=60.0,
            now=100.0,
        )

        assert result == 100.0, "Throttle should return the new timestamp (== now) when the interval has elapsed"

    def test_throttle_uses_real_monotonic_when_now_is_none(self, monkeypatch):
        """When ``now`` is None (the production path), the throttle uses"""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)
        monkeypatch.setattr(resource_probe.time, "monotonic", lambda: 9999.0)

        result = check_resources_throttled(last_check_ts=0.0)

        assert result == 9999.0, "Throttle should fall back to time.monotonic() when now=None and return that value"

    def test_throttle_default_interval_is_60_seconds(self):
        """DEFAULT_CHECK_INTERVAL constant pins the documented 60s"""
        assert DEFAULT_CHECK_INTERVAL == 60.0, (
            "DEFAULT_CHECK_INTERVAL must be 60.0, DictationPipeline.__init__ "
            "uses this as the default for self._resources_check_interval."
        )

    def test_throttle_at_exact_interval_boundary_runs_check(self, monkeypatch):
        """boundary is inclusive of the interval)."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )
        monkeypatch.setattr("os.statvfs", lambda path: _fake_statvfs(50 * 1024**3), raising=False)

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
        """check_resources so records appear under that logger's name"""
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


class TestResourceProbeModuleSurface:
    """Pin the public API of the resource_probe module so accidental"""

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
        """C-DATA-1: the probe performs NO network calls. The module"""
        src = inspect.getsource(resource_probe)
        # Crude but effective: scan import statements for network libs.
        forbidden = ("socket", "urllib", "http.client", "requests", "aiohttp", "httpx")
        for lib in forbidden:
            assert f"import {lib}" not in src, (
                f"C-DATA-1 violation: resource_probe.py must not import network library '{lib}'"
            )
            assert f"from {lib}" not in src, (
                f"C-DATA-1 violation: resource_probe.py must not import from network library '{lib}'"
            )
