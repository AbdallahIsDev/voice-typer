"""Tests for the OS-level cache prewarm pipeline.

These cover the decision logic (guards), the file-warming primitive, and
the CLI entry point — without actually importing torch or reading real
model weights.
"""

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from voice_typer.server import prewarm


# ─── Guards ─────────────────────────────────────────────────────────────


class TestGuards:
    """Config flag and RAM budget guards short-circuit prewarming safely."""

    def test_disabled_config_returns_exit_disabled(self, monkeypatch, tmp_path):
        """When config.fast_startup is False, run() returns EXIT_DISABLED."""
        fake_cfg = MagicMock(fast_startup=False)
        monkeypatch.setattr(
            "voice_typer.server.config.Config.load",
            classmethod(lambda cls: fake_cfg),
        )
        monkeypatch.setattr(prewarm, "_setup_logging", lambda: None)
        assert prewarm.run() == prewarm.EXIT_DISABLED

    def test_enabled_config_proceeds_past_flag(self, monkeypatch):
        """When fast_startup is True, the flag check passes (then other
        guards/import failures take over — we only assert it isn't the
        flag that stopped us)."""
        fake_cfg = MagicMock(fast_startup=True)
        monkeypatch.setattr(
            "voice_typer.server.config.Config.load",
            classmethod(lambda cls: fake_cfg),
        )
        monkeypatch.setattr(prewarm, "_setup_logging", lambda: None)
        # Force a low-RAM skip so we don't do real work.
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: 100)
        result = prewarm.run(min_ram_mb=1024)
        assert result == prewarm.EXIT_LOW_RAM  # flag was NOT the stopper

    def test_low_ram_returns_exit_low_ram(self, monkeypatch):
        """Free RAM below budget → EXIT_LOW_RAM, no prewarming attempted."""
        monkeypatch.setattr(prewarm, "_setup_logging", lambda: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: 512)
        result = prewarm.run(min_ram_mb=4096)
        assert result == prewarm.EXIT_LOW_RAM

    def test_unknown_ram_does_not_skip(self, monkeypatch):
        """If free RAM can't be queried (None), prewarm should NOT bail on
        the RAM guard — it should proceed (and fail later on imports)."""
        monkeypatch.setattr(prewarm, "_setup_logging", lambda: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: None)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        # _warm_imports will raise ImportError on the mocked torch.
        monkeypatch.setattr(
            prewarm, "_warm_imports",
            MagicMock(side_effect=ImportError("no torch")),
        )
        result = prewarm.run()
        assert result == prewarm.EXIT_IMPORT_FAILED

    def test_force_overrides_all_guards(self, monkeypatch):
        """--force skips both config and RAM checks."""
        monkeypatch.setattr(prewarm, "_setup_logging", lambda: None)
        # Even with fast_startup=False and 0 free RAM, force proceeds.
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: False)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: 0)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        monkeypatch.setattr(
            prewarm, "_warm_imports",
            MagicMock(side_effect=ImportError("no torch")),
        )
        result = prewarm.run(force=True)
        assert result == prewarm.EXIT_IMPORT_FAILED


# ─── File warming ────────────────────────────────────────────────────────


class TestWarmFile:
    """_warm_file reads every byte of a file sequentially."""

    def test_warm_file_reads_all_bytes(self, tmp_path):
        """_warm_file returns the exact file size and reads all content."""
        payload = b"\x00\x01\x02" * 1000  # 3000 bytes
        f = tmp_path / "weights.bin"
        f.write_bytes(payload)

        read = prewarm._warm_file(f)
        assert read == len(payload)

    def test_warm_file_empty_file(self, tmp_path):
        """An empty file is a no-op returning 0 bytes read."""
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert prewarm._warm_file(f) == 0

    def test_warm_file_large_file_uses_small_buffer(self, tmp_path, monkeypatch):
        """The 4 MB read buffer is used (verify chunked read by spying on
        the number of read() calls)."""
        # Write 10 MB of data.
        f = tmp_path / "big.bin"
        f.write_bytes(b"\xAB" * (10 * 1024 * 1024))

        # Spy on read calls to confirm chunking.
        original_open = __builtins__["open"] if isinstance(__builtins__, dict) else __builtins__.open
        call_sizes = []

        class _SpyReader:
            def __init__(self, real):
                self._real = real
            def read(self, n=-1):
                call_sizes.append(n)
                return self._real.read(n)
            def __enter__(self):
                self._real.__enter__()
                return self
            def __exit__(self, *a):
                self._real.__exit__(*a)

        def spy_open(path, mode="r", *a, **kw):
            real = original_open(path, mode, *a, **kw)
            if "b" in mode and "r" in mode:
                return _SpyReader(real)
            return real

        monkeypatch.setattr("builtins.open", spy_open)
        prewarm._warm_file(f)

        # All reads except possibly the last should be the chunk size.
        assert prewarm._READ_CHUNK_BYTES in call_sizes
        # 10 MB / 4 MB = 3 full chunks → at least 3 reads at chunk size.
        assert call_sizes.count(prewarm._READ_CHUNK_BYTES) >= 2


# ─── Weights discovery ──────────────────────────────────────────────────


class TestFindWeights:
    """_find_parakeet_weights locates the cached safetensors or returns None."""

    def test_returns_none_when_cache_absent(self, monkeypatch):
        """No cache directory → None."""
        from voice_typer.server import parakeet_engine
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: Path("C:/nonexistent/path/that/does/not/exist"),
        )
        assert prewarm._find_parakeet_weights() is None

    def test_returns_path_when_cached(self, monkeypatch, tmp_path):
        """A snapshot dir with model.safetensors → that path."""
        # Build a fake HF cache layout.
        cache = tmp_path / "huggingface" / "hub"
        model_dir = cache / "models--nvidia--parakeet-tdt-0.6b-v3"
        snap = model_dir / "snapshots" / "abc123"
        snap.mkdir(parents=True)
        weights = snap / "model.safetensors"
        weights.write_bytes(b"fake weights")

        monkeypatch.setattr(
            "voice_typer.server.config._config_dir", lambda: tmp_path
        )
        result = prewarm._find_parakeet_weights()
        assert result == weights

    def test_returns_none_when_snapshot_has_no_weights(self, monkeypatch, tmp_path):
        """Snapshot dir exists but model.safetensors is missing → None."""
        cache = tmp_path / "huggingface" / "hub"
        snap = cache / "models--nvidia--parakeet-tdt-0.6b-v3" / "snapshots" / "abc"
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}")  # no weights

        monkeypatch.setattr(
            "voice_typer.server.config._config_dir", lambda: tmp_path
        )
        assert prewarm._find_parakeet_weights() is None


# ─── CLI ────────────────────────────────────────────────────────────────


class TestCli:
    """The argparse entry point forwards to run()."""

    def test_parse_args_defaults(self):
        args = prewarm._parse_args([])
        assert args.force is False
        assert args.min_ram_mb == prewarm.DEFAULT_MIN_FREE_RAM_MB

    def test_parse_args_force(self):
        args = prewarm._parse_args(["--force"])
        assert args.force is True

    def test_parse_args_custom_ram(self):
        args = prewarm._parse_args(["--min-ram-mb", "2048"])
        assert args.min_ram_mb == 2048

    def test_main_returns_exit_code(self, monkeypatch):
        """main() returns run()'s exit code (disabled config → EXIT_DISABLED)."""
        fake_cfg = MagicMock(fast_startup=False)
        monkeypatch.setattr(
            "voice_typer.server.config.Config.load",
            classmethod(lambda cls: fake_cfg),
        )
        monkeypatch.setattr(prewarm, "_setup_logging", lambda: None)
        # main() calls _parse_args() which reads sys.argv — isolate it.
        monkeypatch.setattr(sys, "argv", ["prewarm"])
        assert prewarm.main() == prewarm.EXIT_DISABLED
