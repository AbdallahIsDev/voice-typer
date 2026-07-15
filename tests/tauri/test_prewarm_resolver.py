"""Tests for the prewarm exe resolver (ADR-0020 §5)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from voice_typer.server import prewarm_resolver


def test_resolve_returns_dev_fallback_when_no_frozen_exe_found(monkeypatch, tmp_path):
    """Without a frozen exe or env override, returns the Python-module command line."""
    # Clear all env vars the resolver checks.
    monkeypatch.delenv("VOICE_TYPER_PREWARM_EXE", raising=False)
    monkeypatch.delenv("TAURI_SIDECAR", raising=False)
    # Force _candidate_paths to return only nonexistent paths.
    monkeypatch.setattr(
        prewarm_resolver,
        "_candidate_paths",
        lambda: [tmp_path / "nonexistent"],
    )

    result = prewarm_resolver.resolve_prewarm_exe()
    assert result is not None
    assert " -m voice_typer.server.prewarm" in result
    assert sys.executable in result


def test_resolve_returns_env_override_path_when_file_exists(monkeypatch, tmp_path):
    """VOICE_TYPER_PREWARM_EXE pointing at a real file wins over all other paths."""
    fake_exe = tmp_path / "prewarm-fake-triple.exe"
    fake_exe.write_text("dummy")

    monkeypatch.setenv("VOICE_TYPER_PREWARM_EXE", str(fake_exe))
    # Even if other candidate paths exist, the env override is first.
    other_exe = tmp_path / "other-prewarm.exe"
    other_exe.write_text("dummy")
    monkeypatch.setattr(
        prewarm_resolver,
        "_candidate_paths",
        lambda: [Path(os.environ["VOICE_TYPER_PREWARM_EXE"]), other_exe],
    )

    result = prewarm_resolver.resolve_prewarm_exe()
    assert result == str(fake_exe)


def test_resolve_skips_nonexistent_env_override(monkeypatch, tmp_path):
    """A broken VOICE_TYPER_PREWARM_EXE falls through to the next candidate."""
    real_exe = tmp_path / "prewarm-real.exe"
    real_exe.write_text("dummy")

    monkeypatch.setenv("VOICE_TYPER_PREWARM_EXE", "/nonexistent/path/prewarm.exe")
    monkeypatch.setattr(
        prewarm_resolver,
        "_candidate_paths",
        lambda: [Path("/nonexistent/path/prewarm.exe"), real_exe],
    )

    result = prewarm_resolver.resolve_prewarm_exe()
    assert result == str(real_exe)


def test_resolve_returns_none_if_sys_executable_empty(monkeypatch):
    """Edge case: sys.executable is empty → can't build dev fallback."""
    monkeypatch.delenv("VOICE_TYPER_PREWARM_EXE", raising=False)
    monkeypatch.setattr(prewarm_resolver, "_candidate_paths", lambda: [])
    monkeypatch.setattr(sys, "executable", "", raising=False)

    result = prewarm_resolver.resolve_prewarm_exe()
    assert result is None


def test_target_triple_matches_platform():
    """The triple must match Tauri's externalBin naming convention."""
    triple = prewarm_resolver._target_triple()
    if sys.platform == "win32":
        assert triple.endswith("-pc-windows-msvc")
    elif sys.platform == "darwin":
        assert triple.endswith("-apple-darwin")
    else:
        assert triple.endswith("-unknown-linux-gnu")


def test_exe_suffix_correct_for_platform():
    """Windows gets .exe; POSIX gets no suffix."""
    suffix = prewarm_resolver._exe_suffix()
    if sys.platform == "win32":
        assert suffix == ".exe"
    else:
        assert suffix == ""


def test_candidate_paths_includes_env_override_first(monkeypatch, tmp_path):
    """VOICE_TYPER_PREWARM_EXE is the first candidate when set."""
    monkeypatch.setenv("VOICE_TYPER_PREWARM_EXE", str(tmp_path / "prewarm.exe"))
    candidates = prewarm_resolver._candidate_paths()
    assert len(candidates) > 0
    assert candidates[0] == Path(os.environ["VOICE_TYPER_PREWARM_EXE"])
