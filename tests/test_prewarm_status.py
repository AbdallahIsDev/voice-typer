"""Unit tests for the RESTORED prewarm status probe.

Covers ``voice_typer/server/prewarm/status.py`` — the user-facing
Cache Status card data (plan §6.3 addendum), restored 2026-08-14
verbatim from commit 5a319872's ``process_tracker.py`` status-query
section, with the sentinel-file machinery replaced by the
worker-written JSON status file (``write_prewarm_status_file``).

These tests mirror the ADR-0009 Issue 3 tests from the original
``tests/test_prewarm.py`` (5a319872, ``TestGetPrewarmStatus``),
adapted to the restored surface:

- the 3-line sentinel file became the worker status file JSON
  (``{"last_run": ..., "elapsed_s": ...}``);
- ``prewarm_running`` (process-tracker machinery) became ``enabled``
  (the ``fast_startup`` config toggle);
- ``active_dirs_exist`` was folded into the probe itself.
"""

from __future__ import annotations

import json

from voice_typer.server.prewarm import status as prewarm_status
from voice_typer.server.prewarm.status import (
    _invalidate_cache_probe_cache,
    get_prewarm_status,
    write_prewarm_status_file,
)


class TestGetPrewarmStatus:
    """ADR-0009 Issue 3: get_prewarm_status() returns a UI-ready dict."""

    def test_no_status_file_and_no_model_dirs_returns_unknown(self, monkeypatch, tmp_path):
        """No worker status file + no model dirs → label='unknown'."""
        monkeypatch.setattr(prewarm_status, "_status_file_path", lambda: tmp_path / "prewarm-status.json")
        monkeypatch.setattr(prewarm_status, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(prewarm_status, "_config_fast_startup", lambda: True)

        status = get_prewarm_status()

        assert status["cache_label"] == "unknown"
        assert status["last_run"] is None
        assert status["elapsed_s"] is None
        assert status["cache_ratio"] == 0.0
        assert status["cached_bytes"] == 0
        assert status["enabled"] is True

    def test_status_file_returns_last_run_and_elapsed(self, monkeypatch, tmp_path):
        """The worker status file feeds last_run + elapsed_s.

        (Mirrors the old 3-line-sentinel test — H2: last_run is the
        wall-clock completion time written by the worker, not a boot
        timestamp.)
        """
        status_file = tmp_path / "prewarm-status.json"
        status_file.write_text(
            json.dumps({"last_run": "2026-08-14T09:12:00", "elapsed_s": 20.4}),
            encoding="utf-8",
        )
        monkeypatch.setattr(prewarm_status, "_status_file_path", lambda: status_file)
        monkeypatch.setattr(prewarm_status, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(prewarm_status, "_config_fast_startup", lambda: True)

        status = get_prewarm_status()

        assert status["last_run"] == "2026-08-14T09:12:00"
        assert status["elapsed_s"] == 20.4
        # Status file present → worker ran → not "unknown" (ratio 0.0 → cold).
        assert status["cache_label"] == "cold"

    def test_corrupt_status_file_degrades_gracefully(self, monkeypatch, tmp_path):
        """A corrupt/unreadable status file degrades to None, never raises."""
        status_file = tmp_path / "prewarm-status.json"
        status_file.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(prewarm_status, "_status_file_path", lambda: status_file)
        monkeypatch.setattr(prewarm_status, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(prewarm_status, "_config_fast_startup", lambda: True)

        status = get_prewarm_status()

        assert status["last_run"] is None
        assert status["elapsed_s"] is None

    def test_enabled_follows_fast_startup_config(self, monkeypatch, tmp_path):
        """``enabled`` mirrors the fast_startup config toggle (start/stop)."""
        monkeypatch.setattr(prewarm_status, "_status_file_path", lambda: tmp_path / "prewarm-status.json")
        monkeypatch.setattr(prewarm_status, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(prewarm_status, "_config_fast_startup", lambda: False)

        assert get_prewarm_status()["enabled"] is False

    def test_cached_bytes_weighted_by_file_size(self, monkeypatch, tmp_path):
        """Review fix H3: cached_bytes is a weighted sum, not total * avg_ratio.

        Without the fix, a 2.4 GB file at 80% + a 1 MB file at 100%
        would report cached_bytes = 2.401 GB * 0.90 = 2.16 GB (wrong).
        With the fix, cached_bytes = 2.4 GB * 0.80 + 1 MB * 1.0 = 1.92 GB.
        """
        cache = tmp_path / "huggingface" / "hub"
        snap_a = cache / "models--a--model" / "snapshots" / "abc"
        snap_a.mkdir(parents=True)
        weights_a = snap_a / "model.safetensors"
        weights_a.write_bytes(b"\x00" * (10 * 1024 * 1024))  # 10 MB
        snap_b = cache / "models--b--model" / "snapshots" / "def"
        snap_b.mkdir(parents=True)
        weights_b = snap_b / "model.safetensors"
        weights_b.write_bytes(b"\x00" * (1 * 1024 * 1024))  # 1 MB

        monkeypatch.setattr(prewarm_status, "_status_file_path", lambda: tmp_path / "prewarm-status.json")
        monkeypatch.setattr(
            prewarm_status,
            "_active_model_cache_dirs",
            lambda: [cache / "models--a--model", cache / "models--b--model"],
        )
        monkeypatch.setattr(prewarm_status, "_config_fast_startup", lambda: True)

        def fake_cache_ratio(path, samples=20):
            if path == weights_a:
                return 1.0
            if path == weights_b:
                return 0.0
            return 0.5

        monkeypatch.setattr(prewarm_status, "_cache_ratio", fake_cache_ratio)
        _invalidate_cache_probe_cache()

        status = get_prewarm_status()

        assert status["total_bytes"] == 11 * 1024 * 1024
        assert status["cached_bytes"] == 10 * 1024 * 1024, (
            f"H3: cached_bytes should be 10 MB (weighted sum), got {status['cached_bytes']} bytes"
        )
        assert status["cache_ratio"] == round(10 / 11, 2)
        assert status["cache_label"] == "hot"  # ratio 0.91 >= 0.9


class TestWritePrewarmStatusFile:
    """The worker-side writer that feeds the card's last-run row."""

    def test_write_then_read_round_trip(self, monkeypatch, tmp_path):
        status_file = tmp_path / "prewarm-status.json"
        monkeypatch.setattr(prewarm_status, "_status_file_path", lambda: status_file)

        write_prewarm_status_file(last_run="2026-08-14T10:00:00", elapsed_s=31.7)

        data = json.loads(status_file.read_text(encoding="utf-8"))
        assert data == {"last_run": "2026-08-14T10:00:00", "elapsed_s": 31.7}

    def test_write_failure_is_best_effort(self, monkeypatch, tmp_path):
        """A write failure never raises (only costs the last-run row)."""
        monkeypatch.setattr(prewarm_status, "_status_file_path", lambda: tmp_path / "no_such_dir" / "x.json")

        write_prewarm_status_file(last_run=None, elapsed_s=0.0)  # must not raise
