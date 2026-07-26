"""ER-FIX-E2: targeted regression tests for the Group 2 prewarm fixes.

These pin the specific behavior changes introduced by the ER-FIX-E2
fix wave so a future refactor that reverts any of them fails loudly.
They complement the broader suites in ``tests/test_prewarm.py``,
``tests/test_prewarm_process_tracker.py``, and
``tests/test_prewarm_xv_fixes.py``.

Coverage
--------
(a) ER-15 — ``_on_battery_and_low_charge`` returns True when battery
    < 60% and not plugged (mock psutil).
(b) ER-51 — ``_warm_package_files`` skips non-whitelisted extensions
    and files under skipped directory names.
(c) ER-52 — ``_warm_imports`` skips ``faster_whisper``/``ctranslate2``
    when ``active_backend != "whisper"`` AND the tiny.en fallback cache
    dir is missing.
(d) ER-68 — cache-ratio probe skips warming when ratio >= 0.9 for
    files > ~100 MB (mock ``_cache_ratio``).

Patch-path bridge
-----------------
Production code routes cross-submodule helper lookups through
``_pkg.X()`` (where ``_pkg`` is the package namespace
``voice_typer.server.prewarm``).  Tests in this file patch helpers on
the package via ``monkeypatch.setattr(prewarm, "X", ...)`` so the
patches take effect for code that lives in the submodule files rather
than the re-exporting ``__init__.py``.
"""

from __future__ import annotations

import sys
from collections import namedtuple
from unittest.mock import MagicMock

import pytest
from voice_typer.server import prewarm

# Real psutil.sensors_battery() returns a named tuple (iterable).
# ``_on_battery_and_low_charge`` unpacks it via
# ``percent, _secs_left, power_plugged = battery``, so the fake must be
# iterable in the same shape.
_FakeBattery = namedtuple("_FakeBattery", ["percent", "secsleft", "power_plugged"])


# ─── ER-15: _on_battery_and_low_charge ───────────────────────────────────


class TestOnBatteryAndLowCharge:
    """ER-15: skip prewarm when the host is on battery and charge < 60%."""

    def test_returns_true_when_unplugged_and_below_threshold(self, monkeypatch):
        """Battery at 30%, unplugged → True (skip prewarm).

        Mocks ``psutil.sensors_battery()`` to return the named-tuple
        shape ``(percent, secs_left, power_plugged)``.
        """
        fake_psutil = MagicMock()
        fake_psutil.sensors_battery = lambda: _FakeBattery(30, 1500, False)
        # ``_on_battery_and_low_charge`` does ``import psutil`` inside
        # the function body, so patch ``sys.modules["psutil"]``.
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

        assert prewarm._on_battery_and_low_charge() is True

    def test_returns_false_when_plugged_in_even_at_low_charge(self, monkeypatch):
        """Battery at 10%, plugged in → False (don't skip — wall power)."""
        fake_psutil = MagicMock()
        fake_psutil.sensors_battery = lambda: _FakeBattery(10, -1, True)
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

        assert prewarm._on_battery_and_low_charge() is False

    def test_returns_false_when_unplugged_but_charge_above_threshold(self, monkeypatch):
        """Battery at 80%, unplugged → False (charge is high enough)."""
        fake_psutil = MagicMock()
        fake_psutil.sensors_battery = lambda: _FakeBattery(80, 7200, False)
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

        assert prewarm._on_battery_and_low_charge() is False

    def test_returns_false_when_no_battery_present(self, monkeypatch):
        """Desktop / server / VM (psutil returns None) → False."""

        fake_psutil = MagicMock()
        fake_psutil.sensors_battery = lambda: None
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

        assert prewarm._on_battery_and_low_charge() is False

    def test_returns_false_when_psutil_not_installed(self, monkeypatch):
        """ImportError → False (don't block prewarm on missing psutil)."""
        # Simulate psutil not being installed by removing it from
        # sys.modules and making any ``import psutil`` raise.
        monkeypatch.delitem(sys.modules, "psutil", raising=False)
        import builtins

        real_import = builtins.__import__

        def _raise_on_psutil(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError("no psutil")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _raise_on_psutil)

        assert prewarm._on_battery_and_low_charge() is False

    def test_pipeline_run_skips_with_exit_on_battery_when_low(self, monkeypatch):
        """ER-15: ``run()`` returns ``EXIT_ON_BATTERY`` (50) when the
        battery guard fires, before any warming work is done."""
        monkeypatch.setattr(prewarm, "_setup_logging", lambda: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)
        # Plenty of RAM — the only reason to skip is the battery guard.
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: 99999)
        # Battery guard fires.
        monkeypatch.setattr(prewarm, "_on_battery_and_low_charge", lambda: True)
        # If the warming pipeline is reached, fail the test fast.
        monkeypatch.setattr(
            prewarm,
            "_lower_io_priority",
            lambda: pytest.fail("ER-15: _lower_io_priority must NOT run when battery guard fires"),
        )
        monkeypatch.setattr(
            prewarm,
            "_write_pid_file",
            lambda: pytest.fail("ER-15: PID file must NOT be written when battery guard fires"),
        )

        result = prewarm.run()
        assert result == prewarm.EXIT_ON_BATTERY, (
            "ER-15: run() must return EXIT_ON_BATTERY (50) when on battery and low charge"
        )

    def test_pipeline_run_force_bypasses_battery_guard(self, monkeypatch):
        """``--force`` bypasses the battery guard (matches the RAM guard
        semantics — force means run unconditionally)."""
        monkeypatch.setattr(prewarm, "_setup_logging", lambda: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: 99999)
        monkeypatch.setattr(prewarm, "_on_battery_and_low_charge", lambda: True)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        monkeypatch.setattr(prewarm, "_write_pid_file", lambda: None)
        monkeypatch.setattr(prewarm, "_remove_pid_file", lambda: None)
        monkeypatch.setattr(
            prewarm,
            "_create_completion_event",
            lambda pid: None,
        )
        monkeypatch.setattr(prewarm, "_signal_completion_event", lambda h: None)
        monkeypatch.setattr(prewarm, "_close_completion_event", lambda h: None)
        # Force the warming pipeline to bail early so we don't do real work.
        monkeypatch.setattr(
            prewarm,
            "_warm_imports",
            MagicMock(side_effect=ImportError("no torch")),
        )

        result = prewarm.run(force=True)
        # Force bypassed the battery guard and reached the warming
        # pipeline, which then bailed with EXIT_IMPORT_FAILED.
        assert result == prewarm.EXIT_IMPORT_FAILED, (
            "ER-15: --force must bypass the battery guard (got "
            f"{result}, expected EXIT_IMPORT_FAILED={prewarm.EXIT_IMPORT_FAILED})"
        )


# ─── ER-51: _warm_package_files extension + directory filter ────────────


class TestWarmPackageFilesFilter:
    """ER-51: ``_warm_package_files`` skips non-whitelisted extensions
    and files under skipped directory names."""
