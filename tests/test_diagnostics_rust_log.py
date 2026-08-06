"""Tests for the diagnostics export bundle's Rust host-log collection.

Covers the diagnostics-side half of the cross-language log-path fix:
``scripts/diagnostics.py export`` previously collected only the Python
host log (``<config_dir>/voice-typer.log``) and silently omitted the
Rust/Tauri host log (``<config_dir>/logs/voice-typer.log`` +
``.log.1`` … ``.log.4``). The fix routes both through
``_collect_log_tail`` and names the Rust logs with a ``rust-`` prefix
so they're distinguishable in the zip.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_sounddevice(monkeypatch):
    """Headless mock so importing ``voice_typer.server.config`` doesn't touch audio HW."""
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)


def _make_bundle(tmp_path: Path, monkeypatch, config_dir: Path) -> Path:
    """Run ``export_diagnostics()`` with a temp CWD and return the zip path.

    The zip is created in CWD by ``export_diagnostics``; we monkeypatch
    ``Path.cwd`` via chdir to a temp dir so the test doesn't litter the
    repo root.
    """
    # ``export_diagnostics`` writes to Path.cwd(). chdir to a temp output dir.
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    monkeypatch.chdir(out_dir)

    # Point _config_dir at our prepared config dir.
    monkeypatch.setattr(
        "voice_typer.server.config._config_dir",
        lambda: config_dir,
    )

    # Avoid importing torch / ctranslate2 / huggingface_hub in CI.
    for mod in ("torch", "ctranslate2", "huggingface_hub"):
        monkeypatch.setitem(sys.modules, mod, None)

    # Import after patches are in place.
    from scripts.diagnostics import export_diagnostics

    zip_path = export_diagnostics()
    return Path(zip_path)


class TestExportCollectsRustLog:
    """``export_diagnostics`` must include the Rust host log, not just Python's."""

    def test_bundle_includes_python_log(self, tmp_path, monkeypatch):
        """Sanity: the Python log is still collected (regression guard)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "voice-typer.log").write_text("python log line\n", encoding="utf-8")

        zip_path = _make_bundle(tmp_path, monkeypatch, config_dir)

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert "voice-typer.log" in names, f"Python log missing from bundle: {names}"
            content = zf.read("voice-typer.log").decode("utf-8")
            assert "python log line" in content

    def test_bundle_includes_rust_current_log(self, tmp_path, monkeypatch):
        """The Rust host log at ``logs/voice-typer.log`` must be collected."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "voice-typer.log").write_text("python\n", encoding="utf-8")
        logs_dir = config_dir / "logs"
        logs_dir.mkdir()
        (logs_dir / "voice-typer.log").write_text("rust host log line\n", encoding="utf-8")

        zip_path = _make_bundle(tmp_path, monkeypatch, config_dir)

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert "rust-voice-typer.log" in names, f"Rust current log missing from bundle: {names}"
            content = zf.read("rust-voice-typer.log").decode("utf-8")
            assert "rust host log line" in content

    def test_bundle_includes_rotated_rust_logs(self, tmp_path, monkeypatch):
        """Rotated Rust logs (``.log.1`` … ``.log.4``) must be collected too."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "voice-typer.log").write_text("python\n", encoding="utf-8")
        logs_dir = config_dir / "logs"
        logs_dir.mkdir()
        (logs_dir / "voice-typer.log").write_text("current\n", encoding="utf-8")
        # newline="" writes the exact bytes (no \n → \r\n translation
        # on Windows) so the zip content comparison stays byte-exact.
        (logs_dir / "voice-typer.log.1").write_text("rotated-1\n", encoding="utf-8", newline="")
        (logs_dir / "voice-typer.log.2").write_text("rotated-2\n", encoding="utf-8", newline="")

        zip_path = _make_bundle(tmp_path, monkeypatch, config_dir)

        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            assert "rust-voice-typer.log" in names
            assert "rust-voice-typer.log.1" in names, f"missing rotated log .1 in {sorted(names)}"
            assert "rust-voice-typer.log.2" in names, f"missing rotated log .2 in {sorted(names)}"
            assert zf.read("rust-voice-typer.log.1").decode("utf-8") == "rotated-1\n"
            assert zf.read("rust-voice-typer.log.2").decode("utf-8") == "rotated-2\n"

    def test_bundle_omits_rust_log_when_absent(self, tmp_path, monkeypatch):
        """If no Rust log exists, the bundle simply doesn't include it (no crash, no placeholder)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "voice-typer.log").write_text("python only\n", encoding="utf-8")
        # No logs/ dir at all.

        zip_path = _make_bundle(tmp_path, monkeypatch, config_dir)

        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            assert "voice-typer.log" in names
            assert not any(n.startswith("rust-") for n in names), (
                f"no Rust log files should be present: {sorted(names)}"
            )


class TestCollectLogTail:
    """Unit tests for the ``_collect_log_tail`` helper."""

    def test_skips_missing_source(self, tmp_path):
        from scripts.diagnostics import _collect_log_tail

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        # Source doesn't exist — no-op (no destination file created).
        _collect_log_tail(tmp_path / "nonexistent.log", dest_dir, "out.log")
        assert not (dest_dir / "out.log").exists()

    def test_copies_small_file_verbatim(self, tmp_path):
        from scripts.diagnostics import _collect_log_tail

        src = tmp_path / "src.log"
        src.write_text("hello world\n", encoding="utf-8")
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        _collect_log_tail(src, dest_dir, "out.log")
        assert (dest_dir / "out.log").read_text(encoding="utf-8") == "hello world\n"

    def test_truncates_large_file_to_tail(self, tmp_path):
        from scripts.diagnostics import _collect_log_tail

        src = tmp_path / "src.log"
        # Write ~2MB of realistic log lines (each ~100 bytes), then a marker.
        # Using real newlines exercises the readline()-skips-partial-line
        # logic in _collect_log_tail.
        line = "x" * 90 + "\n"  # 91 bytes per line
        bulk = line * ((2 * 1024 * 1024) // len(line))  # ~2MB
        marker = "MARKER_LINE_AT_END\n"
        src.write_text(bulk + marker, encoding="utf-8")
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        _collect_log_tail(src, dest_dir, "out.log", max_bytes=1024 * 1024)
        out = (dest_dir / "out.log").read_text(encoding="utf-8")
        # The tail must include the marker; the partial first line after the
        # seek is skipped, so the output is the rest of the tail (≤1MB).
        assert "MARKER_LINE_AT_END" in out
        assert len(out) <= 1024 * 1024 + len(marker)

    def test_writes_placeholder_on_read_error(self, tmp_path):
        from scripts.diagnostics import _collect_log_tail

        src = tmp_path / "src.log"
        src.write_text("ok\n", encoding="utf-8")
        # Make the file unreadable by turning it into a directory (open() raises).
        src.unlink()
        src.mkdir()
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        _collect_log_tail(src, dest_dir, "out.log")
        out = (dest_dir / "out.log").read_text(encoding="utf-8")
        assert "Error reading log" in out
