"""Regression tests for ``scripts/build/generate_beeps.py --check``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Resolve the script path relative to the repo root (tests/ is one
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "build" / "generate_beeps.py"


def _load_generate_beeps():
    """Load ``generate_beeps.py`` as an isolated module."""
    spec = importlib.util.spec_from_file_location("generate_beeps_under_test", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_sound_manager(path: Path, start_url: str, stop_url: str) -> None:
    """Write a minimal sound-manager.ts with the two constants."""
    path.write_text(
        "/* preamble */\n"
        f'const START_BEEP_WAV =\n\t"{start_url}";\n'
        f'const STOP_BEEP_WAV =\n\t"{stop_url}";\n'
        "/* epilogue */\n",
        encoding="utf-8",
    )


def test_check_passes_on_real_sound_manager(capsys):
    """Smoke test: ``--check`` exits 0 against the real, healthy source file."""
    mod = _load_generate_beeps()
    original_argv = sys.argv
    sys.argv = ["generate_beeps.py", "--check"]
    try:
        rc = mod.main()
    finally:
        sys.argv = original_argv
    out = capsys.readouterr()
    assert rc == 0, (
        f"--check should pass on the real sound-manager.ts; got rc={rc}.\nstderr:\n{out.err}\nstdout:\n{out.out}"
    )


def test_check_passes_when_constants_match_generated(monkeypatch, tmp_path, capsys):
    """``--check`` exits 0 when a fake sound-manager.ts carries the"""
    mod = _load_generate_beeps()
    start_url = mod.generate_start_url()
    stop_url = mod.generate_stop_url()
    fake_sm = tmp_path / "sound-manager.ts"
    _write_sound_manager(fake_sm, start_url, stop_url)
    monkeypatch.setattr(mod, "SOUND_MANAGER_PATH", fake_sm)

    original_argv = sys.argv
    sys.argv = ["generate_beeps.py", "--check"]
    try:
        rc = mod.main()
    finally:
        sys.argv = original_argv
    assert rc == 0


def test_check_fails_when_constants_are_identical(monkeypatch, tmp_path, capsys):
    """``--check`` exits 1 when the two committed constants are byte-for-byte"""
    mod = _load_generate_beeps()
    bogus = "data:audio/wav;base64,AAAA"
    fake_sm = tmp_path / "sound-manager.ts"
    _write_sound_manager(fake_sm, bogus, bogus)
    monkeypatch.setattr(mod, "SOUND_MANAGER_PATH", fake_sm)

    original_argv = sys.argv
    sys.argv = ["generate_beeps.py", "--check"]
    try:
        rc = mod.main()
    finally:
        sys.argv = original_argv
    err = capsys.readouterr().err
    assert rc == 1, f"--check should fail when committed constants are identical; got rc={rc}.\nstderr:\n{err}"
    assert "identical" in err.lower(), f"stderr should mention 'identical'; got:\n{err}"


def test_check_fails_when_constants_drift(monkeypatch, tmp_path, capsys):
    """half-applied ``--write`` or a hand-edit that only touched one"""
    mod = _load_generate_beeps()
    # Two distinct bogus URLs that do NOT match the freshly generated
    bogus_start = "data:audio/wav;base64,AAAA"
    bogus_stop = "data:audio/wav;base64,BBBB"
    fake_sm = tmp_path / "sound-manager.ts"
    _write_sound_manager(fake_sm, bogus_start, bogus_stop)
    monkeypatch.setattr(mod, "SOUND_MANAGER_PATH", fake_sm)

    original_argv = sys.argv
    sys.argv = ["generate_beeps.py", "--check"]
    try:
        rc = mod.main()
    finally:
        sys.argv = original_argv
    err = capsys.readouterr().err
    assert rc == 1, (
        f"--check should fail when committed constants drift from the generated URLs; got rc={rc}.\nstderr:\n{err}"
    )
    # The error message should mention "match" or "drift", both
    assert "match" in err.lower() or "drift" in err.lower(), f"stderr should mention match/drift; got:\n{err}"


def test_check_fails_when_sound_manager_missing(monkeypatch, tmp_path, capsys):
    """``--check`` exits 1 when sound-manager.ts is missing entirely."""
    mod = _load_generate_beeps()
    missing = tmp_path / "does-not-exist.ts"
    monkeypatch.setattr(mod, "SOUND_MANAGER_PATH", missing)

    original_argv = sys.argv
    sys.argv = ["generate_beeps.py", "--check"]
    try:
        rc = mod.main()
    finally:
        sys.argv = original_argv
    err = capsys.readouterr().err
    assert rc == 1
    assert "not found" in err.lower()


def test_read_sound_manager_urls_returns_full_data_urls():
    """``_read_sound_manager_urls`` reconstructs the full ``data:audio/wav;base64,...``"""
    mod = _load_generate_beeps()
    start_url, stop_url = mod._read_sound_manager_urls()
    assert start_url.startswith("data:audio/wav;base64,")
    assert stop_url.startswith("data:audio/wav;base64,")
    assert start_url != stop_url


if __name__ == "__main__":
    # Allow running this test file directly: ``python tests/test_beeps_check_regression.py``
    sys.exit(pytest.main([__file__, "-v", "--no-cov"]))
