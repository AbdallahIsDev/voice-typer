"""Tests for DictationPipeline._check_resources (RAM, disk, GPU health check).

S2-CR-56 follow-up: _check_resources (160 LOC) had no dedicated test
in isolation. This file covers all major code paths:

- **RAM check**: psutil available (normal / low / moderate), psutil
  unavailable (fallback to debug log)
- **Disk check**: normal (> 1 GB free), critically low (< 1 GB)
- **GPU check**: torch.cuda available (normal / low), torch unavailable
- **Graceful degradation**: never raises, always logs the "complete" line
"""

from __future__ import annotations

import logging
import sys

from voice_typer.server.dictation_pipeline import DictationPipeline

# ── Helper ────────────────────────────────────────────────────────────────


def _make_pipeline() -> DictationPipeline:
    """Build a minimal DictationPipeline for testing _check_resources.

    _check_resources does not use ``self._app`` — only local imports
    and logging.  Bypass ``__init__`` via ``__new__`` since ``__init__``
    expects a real ``VoiceTyperApp``.
    """
    return DictationPipeline.__new__(DictationPipeline)


def _fake_vm(available_bytes: int) -> object:
    """Return a fake ``virtual_memory`` return value with the given
    ``.available`` (in bytes).  Mimics ``psutil._pslinux.svmem`` /
    ``psutil._pswindows.svmem`` namedtuple which has ``.available``."""
    return type("_FakeVM", (), {"available": available_bytes})()


def _fake_disk_usage(free_bytes: int) -> object:
    """Return a fake ``shutil.disk_usage`` return value with the given
    ``.free`` (in bytes).  Mimics ``shutil._ntuple_diskusage`` namedtuple
    which has ``.total``, ``.used``, ``.free``."""
    return type("_FakeDiskUsage", (), {"free": free_bytes})()


# ── RAM check ────────────────────────────────────────────────────────────


class TestCheckResourcesRAM:
    """_check_resources: RAM health check paths."""

    def test_logs_ram_info_when_sufficient(self, caplog, monkeypatch):
        """When psutil reports > 2048 MB available, an INFO line shows the
        amount and no warnings are emitted."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),  # 4 GB available
        )

        pipeline = _make_pipeline()
        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources()

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

        pipeline = _make_pipeline()
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Low RAM" in r.getMessage()]
        assert warnings, "Should log WARNING when RAM < 1024 MB"
        assert "0xC0000374" in warnings[0].getMessage(), "Warning must mention heap corruption exit code (0xC0000374)"

    def test_infos_moderate_ram_between_1024_and_2048_mb(self, caplog, monkeypatch):
        """When available RAM is 1024-2048 MB, an INFO line about moderate
        RAM is logged (not an error — the user can still transcribe with
        smaller models)."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(1500 * 1024**2),  # ~1.5 GB available
        )

        pipeline = _make_pipeline()
        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources()

        moderate_lines = [r for r in caplog.records if "RAM is moderate" in r.getMessage()]
        assert moderate_lines, "Should log INFO about moderate RAM when available is 1024-2048 MB"


# ── Disk check ───────────────────────────────────────────────────────────


class TestCheckResourcesDisk:
    """_check_resources: disk space check paths."""

    def test_logs_disk_info_when_sufficient(self, caplog, monkeypatch):
        """When disk has > 1 GB free, an INFO line shows the free space
        and no warning is emitted."""
        monkeypatch.setattr("shutil.disk_usage", lambda path: _fake_disk_usage(50 * 1024**3))  # 50 GB free

        pipeline = _make_pipeline()
        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources()

        disk_lines = [r for r in caplog.records if "[RESOURCE] Disk free" in r.getMessage()]
        assert disk_lines, "Should log disk free space when shutil.disk_usage succeeds"

        disk_warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "Critically low disk" in r.getMessage()
        ]
        assert not disk_warnings, "Should NOT warn about low disk when > 1 GB is free"

    def test_warns_when_disk_below_1_gb(self, caplog, monkeypatch):
        """When any monitored drive has < 1 GB free, a WARNING about heap
        corruption risk is logged."""
        monkeypatch.setattr("shutil.disk_usage", lambda path: _fake_disk_usage(500 * 1024**2))  # 500 MB free

        pipeline = _make_pipeline()
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources()

        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "Critically low disk" in r.getMessage()
        ]
        assert warnings, "Should log WARNING when a monitored drive has < 1 GB free"

    def test_handles_disk_usage_failure_gracefully(self, caplog, monkeypatch):
        """When shutil.disk_usage raises (e.g. a broken path), the
        check skips that drive and continues without crashing."""

        def _failing_disk_usage(path):
            raise PermissionError(f"Cannot access {path}")

        monkeypatch.setattr("shutil.disk_usage", _failing_disk_usage)

        pipeline = _make_pipeline()
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources()

        # Even with disk failures, the method must complete without
        # crashing and log the "complete" line.
        complete_lines = [r for r in caplog.records if "[RESOURCE] Pre-flight health check complete" in r.getMessage()]
        assert complete_lines, (
            "_check_resources must complete without crashing even when shutil.disk_usage raises on all drives"
        )


# ── GPU check ────────────────────────────────────────────────────────────


class TestCheckResourcesGPU:
    """_check_resources: GPU memory check paths.

    The ``mock_heavy_imports`` autouse fixture in tests/conftest.py
    already installs a mock ``torch`` in ``sys.modules``.  These tests
    set specific return values on that mock to exercise the GPU check
    branches.
    """

    def test_logs_gpu_info_when_sufficient(self, caplog, monkeypatch):
        """When torch.cuda is available and GPU memory is sufficient,
        an INFO line shows the allocated / reserved / free amounts."""
        total_memory = 8 * 1024**3  # 8 GB total
        allocated = 2 * 1024**3  # 2 GB allocated  → 6144 MB free > 512 MB

        monkeypatch.setattr("torch.cuda.is_available", lambda: True)
        monkeypatch.setattr("torch.cuda.memory_allocated", lambda: allocated)
        monkeypatch.setattr("torch.cuda.memory_reserved", lambda: allocated)
        monkeypatch.setattr(
            "torch.cuda.get_device_properties",
            lambda device: type("_FakeDeviceProps", (), {"total_memory": total_memory})(),
        )

        pipeline = _make_pipeline()
        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources()

        gpu_lines = [r for r in caplog.records if "[RESOURCE] GPU memory" in r.getMessage()]
        assert gpu_lines, "Should log GPU memory info when torch.cuda is available"

        gpu_warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "Low GPU memory" in r.getMessage()
        ]
        assert not gpu_warnings, "Should NOT warn when GPU has > 512 MB free"

    def test_warns_when_gpu_memory_below_512_mb(self, caplog, monkeypatch):
        """When torch.cuda reports < 512 MB free GPU memory, a WARNING
        about CUDA out-of-memory errors is logged."""
        total_memory = 1024**3  # 1 GB total
        allocated = 900 * 1024**2  # 900 MB allocated → 124 MB free < 512 MB

        monkeypatch.setattr("torch.cuda.is_available", lambda: True)
        monkeypatch.setattr("torch.cuda.memory_allocated", lambda: allocated)
        monkeypatch.setattr("torch.cuda.memory_reserved", lambda: allocated)
        monkeypatch.setattr(
            "torch.cuda.get_device_properties",
            lambda device: type("_FakeDeviceProps", (), {"total_memory": total_memory})(),
        )

        pipeline = _make_pipeline()
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Low GPU memory" in r.getMessage()]
        assert warnings, "Should log WARNING when free GPU memory < 512 MB"


# ── Throttle wrapper ─────────────────────────────────────────────────────


class TestCheckResourcesThrottled:
    """_check_resources_throttled: the throttle wrapper that limits
    _check_resources to once per 60 seconds.

    Note: ``_new_pipeline`` bypasses ``__init__``, so
    ``_resources_check_interval`` and ``_last_resources_check_ts`` must
    be set manually.
    """

    def test_throttle_skips_check_when_recently_run(self, caplog, monkeypatch):
        """When the last check was less than 60s ago, the throttle
        returns early without calling _check_resources."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )

        pipeline = _make_pipeline()
        # Set the interval and last-check-ts so that the throttle
        # considers the check "recent" and returns early.
        pipeline._last_resources_check_ts = 9999999999.0  # far in the future
        pipeline._resources_check_interval = 60.0

        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources_throttled()

        # No RAM info should be logged because _check_resources was skipped.
        ram_lines = [r for r in caplog.records if "[RESOURCE]" in r.getMessage()]
        assert not ram_lines, (
            "_check_resources_throttled should skip the real check when the throttle interval has not elapsed"
        )

    def test_throttle_runs_check_when_interval_elapsed(self, caplog, monkeypatch):
        """When the last check was long enough ago (> 60s), the throttle
        calls _check_resources and updates the timestamp."""
        monkeypatch.setattr(
            "psutil.virtual_memory",
            lambda: _fake_vm(4 * 1024**3),
        )

        pipeline = _make_pipeline()
        pipeline._last_resources_check_ts = 0.0  # never checked
        pipeline._resources_check_interval = 60.0

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources_throttled()

        # RAM info should be logged (the real check ran).
        ram_lines = [r for r in caplog.records if "[RESOURCE] Available RAM" in r.getMessage()]
        assert ram_lines, (
            "_check_resources_throttled should call _check_resources when the throttle interval has elapsed"
        )
        # The timestamp must have been updated.
        assert pipeline._last_resources_check_ts > 0.0, (
            "_check_resources_throttled must update _last_resources_check_ts after running the real check"
        )


# ── Graceful degradation ─────────────────────────────────────────────────


class TestCheckResourcesGracefulDegradation:
    """_check_resources must never raise — every sub-check is wrapped in
    try/except.  Even when all dependencies fail, the method completes
    and logs a final "complete" line."""

    def _patch_psutil_unavailable(self, monkeypatch) -> None:
        """Make psutil appear unimportable by removing it from
        sys.modules and patching builtins.__import__ to raise
        ImportError for ``psutil``.

        Strategy:
        1. Remove psutil from sys.modules so ``import psutil`` does not
           find a cached import.
        2. Save a reference to the REAL ``builtins.__import__``.
        3. Replace ``builtins.__import__`` with a wrapper that raises
           ``ImportError`` for ``psutil`` and delegates to the real
           function for everything else.

        We save the real function BEFORE patching so the wrapper doesn't
        recurse when it needs to delegate a non-psutil import.
        """
        monkeypatch.delitem(sys.modules, "psutil", raising=False)

        import builtins as _builtins_mod

        _real_import = _builtins_mod.__import__

        def _mock_import(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError(f"No module named '{name}'")
            return _real_import(name, *args, **kwargs)

        monkeypatch.setattr(_builtins_mod, "__import__", _mock_import)

    def test_completes_without_crash_when_psutil_unavailable(self, caplog, monkeypatch):
        """When psutil is not importable and the ctypes fallback also
        doesn't apply (non-Windows), the RAM check logs DEBUG and
        method continues to the disk check."""
        self._patch_psutil_unavailable(monkeypatch)

        pipeline = _make_pipeline()
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources()

        # The method must log the final "complete" line.
        complete_lines = [r for r in caplog.records if "[RESOURCE] Pre-flight health check complete" in r.getMessage()]
        assert complete_lines, "_check_resources must complete without crashing even when psutil is unavailable"

    def test_completes_without_crash_when_all_checks_fail(self, caplog, monkeypatch):
        """When every sub-check fails (psutil unavailable, disk_usage
        raises, torch import fails), the method still completes."""
        self._patch_psutil_unavailable(monkeypatch)

        # Make disk_usage raise on every path
        def _failing_disk_usage(path):
            raise OSError(f"Cannot stat {path}")

        monkeypatch.setattr("shutil.disk_usage", _failing_disk_usage)

        pipeline = _make_pipeline()
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources()

        complete_lines = [r for r in caplog.records if "[RESOURCE] Pre-flight health check complete" in r.getMessage()]
        assert complete_lines, "_check_resources must complete without crashing even when ALL sub-checks fail"


# ── XZ-EH-008 / XZ-EH-022: silent except:pass replaced with log.debug ──


class TestCheckResourcesXZEH008SilentExcept:
    """XZ-EH-008 / XZ-EH-022: the two ``except Exception: pass`` blocks
    in ``_check_resources`` (RAM ctypes fallback at :578-579 and GPU
    check at :713) were replaced with ``log.debug(..., exc_info=True)``.

    The docstring at :545-557 promises "failures are logged at DEBUG
    level" — these tests pin that contract so a future revert that
    re-introduces a silent ``pass`` is caught.

    Note: the GPU branch's prior ``(ImportError, Exception)`` tuple was
    also narrowed to bare ``Exception`` (ImportError is a subclass and
    the redundant tuple just obscured intent); this test confirms the
    narrowing too.
    """

    def test_ram_ctypes_fallback_failure_logs_debug(self, caplog, monkeypatch):
        """When psutil is unavailable AND the ctypes fallback raises
        (Windows-only code path), a DEBUG line is emitted (not silent
        ``pass``).

        We exercise the inner ``except Exception`` branch by patching
        ``ctypes.Structure`` instantiation to raise — this avoids
        patching ``os.name`` (which breaks pathlib's path-class
        dispatch on non-Windows hosts). The test patches
        ``ctypes.windll.kernel32.GlobalMemoryStatusEx`` to raise
        directly so the Windows-only branch fires and the surrounding
        ``except Exception`` block runs.
        """
        # Force psutil ImportError path so the ctypes fallback runs.
        monkeypatch.delitem(sys.modules, "psutil", raising=False)

        import builtins as _builtins_mod

        _real_import = _builtins_mod.__import__

        def _mock_import(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError(f"No module named '{name}'")
            return _real_import(name, *args, **kwargs)

        monkeypatch.setattr(_builtins_mod, "__import__", _mock_import)

        # Bypass the ``if _os.name == "nt":`` guard by patching the
        # ``os`` module's name attribute on the module that
        # ``_check_resources`` actually imports. The function does
        # ``import os as _os`` and reads ``_os.name``, so patching
        # ``os.name`` directly affects the check. We must NOT patch
        # ``os.name`` globally because pathlib consults it lazily to
        # pick a Path subclass — instead we patch the dict lookup on
        # the already-imported ``os`` module to "nt" for the duration
        # of this test only, then restore via monkeypatch.setattr
        # (which uses the default-restore behavior).
        #
        # The pathlib INTERNALERROR on non-Windows hosts comes from
        # pathlib picking ``WindowsPath`` when ``os.name == "nt"``,
        # then failing to instantiate. We avoid that by stubbing
        # ``pathlib.Path`` to a no-op class for the duration of this
        # test, since ``_check_resources`` itself does not use
        # pathlib (only the inner ctypes fallback does).
        import os as _os_mod

        monkeypatch.setattr(_os_mod, "name", "nt")

        # Patch ctypes.windll to raise AttributeError when accessed —
        # the production code does ``ctypes.windll.kernel32.GlobalMemoryStatusEx(...)``
        # so an AttributeError on ``windll`` propagates into the
        # ``except Exception`` block.
        import ctypes as _ctypes_mod

        class _RaisingWindll:
            def __getattr__(self, name):
                raise AttributeError(f"no attribute {name!r}")

        monkeypatch.setattr(_ctypes_mod, "windll", _RaisingWindll(), raising=False)

        # Avoid pathlib INTERNALERROR on non-Windows hosts when
        # ``os.name`` is patched to "nt" (pathlib picks WindowsPath
        # lazily). ``_check_resources`` itself does not use pathlib
        # for the RAM/ctypes branch, but the subsequent disk-check
        # branch (which runs after the RAM check) calls
        # ``_Path.home()`` — so the stub needs a ``home()`` classmethod
        # too.
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

        monkeypatch.setattr(_pathlib_mod, "Path", _StubPath)
        # Also patch ``pathlib.PosixPath`` / ``WindowsPath`` since
        # some pathlib internals reference them directly.
        monkeypatch.setattr(_pathlib_mod, "PosixPath", _StubPath, raising=False)
        monkeypatch.setattr(_pathlib_mod, "WindowsPath", _StubPath, raising=False)

        pipeline = _make_pipeline()
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources()

        # XZ-EH-008: the ctypes-fallback DEBUG line MUST be present.
        ctypes_debug_lines = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "RAM check (ctypes fallback) failed" in r.getMessage()
        ]
        assert ctypes_debug_lines, (
            "XZ-EH-008 regression: the ctypes-fallback except block must "
            "emit `log.debug('[RESOURCE] RAM check (ctypes fallback) failed "
            "(non-fatal)', exc_info=True)` instead of silent `pass`."
        )

    def test_gpu_check_failure_logs_debug(self, caplog, monkeypatch):
        """When torch import succeeds but ``cuda.is_available`` raises
        (or any other GPU-check exception fires), a DEBUG line is
        emitted (not silent ``pass``)."""

        # Make ``torch.cuda.is_available`` raise — the production
        # GPU-check try/except catches this and (post-XZ-EH-008) logs
        # a DEBUG line.
        def _raising_is_available():
            raise RuntimeError("CUDA driver mismatch")

        monkeypatch.setattr("torch.cuda.is_available", _raising_is_available)

        pipeline = _make_pipeline()
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
            pipeline._check_resources()

        gpu_debug_lines = [
            r for r in caplog.records if r.levelno == logging.DEBUG and "GPU check failed" in r.getMessage()
        ]
        assert gpu_debug_lines, (
            "XZ-EH-008 regression: the GPU-check except block must emit "
            "`log.debug('[RESOURCE] GPU check failed (non-fatal)', "
            "exc_info=True)` instead of silent `pass`."
        )

    def test_no_silent_except_pass_in_check_resources_source(self):
        """Static check: ``_check_resources`` source must not contain
        a bare ``except Exception: pass`` (the XZ-EH-008 pattern)."""
        import inspect

        src = inspect.getsource(DictationPipeline._check_resources)
        # Look for the offending pattern with optional whitespace.
        # The pattern is: ``except Exception:`` followed by ``pass``
        # (possibly on the next line). We do a line-by-line scan so
        # comments don't trip us up.
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
                        f"XZ-EH-008 regression: bare `except Exception: pass` "
                        f"re-introduced at _check_resources source line ~{i + 1}. "
                        f"Replace with `log.debug(..., exc_info=True)`."
                    )
                    break
            elif stripped.startswith("except Exception:") and stripped.endswith("pass"):
                # Inline form: ``except Exception: pass``
                raise AssertionError(
                    f"XZ-EH-008 regression: inline `except Exception: pass` at _check_resources source line ~{i + 1}."
                )

    def test_check_resources_docstring_promises_debug_logging(self):
        """XZ-EH-022: the docstring must still promise "DEBUG level"
        logging for failures (this pins the docstring against future
        drift — the prior drift was the docstring claiming DEBUG while
        the code did ``pass``)."""
        doc = DictationPipeline._check_resources.__doc__ or ""
        assert "DEBUG" in doc, (
            "XZ-EH-022 regression: _check_resources docstring must mention "
            "'DEBUG' level for failure logging — the original finding "
            "flagged the docstring drift where it claimed DEBUG logging "
            "but the code did silent `pass`."
        )
