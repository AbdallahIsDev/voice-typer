"""Tests for the prewarm exe resolver (ADR-0020 §5)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
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


@pytest.mark.skipif(sys.platform != "linux", reason="linux-only target triple")
def test_target_triple_linux():
    """The triple ends with '-unknown-linux-gnu' on Linux.

    Replaces the previous EC-26 silent ``if sys.platform == ...`` guard
    with an explicit per-platform test so a non-Linux run reports a
    SKIP (not a silent PASS) — the orchestrator's acceptance criteria
    require visibility into which platform branches actually executed.
    """
    triple = prewarm_resolver._target_triple()
    assert triple.endswith("-unknown-linux-gnu")


@pytest.mark.skipif(sys.platform != "win32", reason="windows-only target triple")
def test_target_triple_windows():
    """The triple ends with '-pc-windows-msvc' on Windows."""
    triple = prewarm_resolver._target_triple()
    assert triple.endswith("-pc-windows-msvc")


@pytest.mark.skipif(sys.platform != "darwin", reason="macos-only target triple")
def test_target_triple_macos():
    """The triple ends with '-apple-darwin' on macOS."""
    triple = prewarm_resolver._target_triple()
    assert triple.endswith("-apple-darwin")


def test_target_triple_aarch64_linux_uses_rust_convention(monkeypatch):
    """ADR-0020 §4.1: on Linux ARM64, the triple must be `aarch64-unknown-linux-gnu`.

    Regression: an earlier implementation used `sys.maxsize > 2**32` for
    the arch check, which only distinguishes x86_64 from x86 — it never
    returned `aarch64` on Linux ARM64 hosts. The fix uses
    `platform.machine()` (which returns 'aarch64' on Linux ARM64) so the
    triple matches the Rust target name exactly.

    Also verifies the triple does NOT use any of the wrong forms:
      - `arm64-unknown-linux-gnu` (Apple's name, not Rust's)
      - `aarch64-linux-gnu` (Debian's name, not Rust's target triple)
      - `aarch64-unknown-linux-musl` (musl variant — not used by Voice Typer)
    """
    import platform as _platform

    # Force Linux + aarch64.
    monkeypatch.setattr(prewarm_resolver, "is_windows", lambda: False)
    monkeypatch.setattr(prewarm_resolver, "is_macos", lambda: False)
    monkeypatch.setattr(prewarm_resolver, "is_linux", lambda: True)
    monkeypatch.setattr(_platform, "machine", lambda: "aarch64")

    triple = prewarm_resolver._target_triple()
    assert triple == "aarch64-unknown-linux-gnu", (
        f"Linux ARM64 triple must be 'aarch64-unknown-linux-gnu' (got '{triple}'). "
        "ADR-0020 §4.1 mandates the Rust target-triple naming convention."
    )
    # Explicitly check the wrong forms are NOT returned.
    assert triple != "arm64-unknown-linux-gnu", "Apple's 'arm64' name is wrong for Rust"
    assert triple != "aarch64-linux-gnu", "Debian's 'aarch64-linux-gnu' is not a Rust target triple"
    assert triple != "aarch64-unknown-linux-musl", "musl variant is not used by Voice Typer"


def test_target_triple_x86_64_linux(monkeypatch):
    """ADR-0020 §4.1: on Linux x86_64, the triple must be `x86_64-unknown-linux-gnu`."""
    import platform as _platform

    monkeypatch.setattr(prewarm_resolver, "is_windows", lambda: False)
    monkeypatch.setattr(prewarm_resolver, "is_macos", lambda: False)
    monkeypatch.setattr(prewarm_resolver, "is_linux", lambda: True)
    monkeypatch.setattr(_platform, "machine", lambda: "x86_64")

    triple = prewarm_resolver._target_triple()
    assert triple == "x86_64-unknown-linux-gnu"


def test_target_triple_apple_silicon_returns_aarch64(monkeypatch):
    """Apple Silicon (platform.machine() == 'arm64') MUST return
    'aarch64-apple-darwin' — NOT 'arm64-apple-darwin'.

    This is the Rust target-triple naming convention used by Tauri's
    externalBin mechanism. ADR-0020 §4.1 explicitly lists
    'aarch64-apple-darwin' (NOT 'arm64-apple-darwin') as the macOS
    Apple Silicon target triple. The original ADR §5 code snippet had
    a bug returning 'arm64-apple-darwin' — this test guards against
    regression.
    """
    import platform as _platform

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(_platform, "machine", lambda: "arm64")
    # is_macos() reads sys.platform directly via platform_utils.
    triple = prewarm_resolver._target_triple()
    assert triple == "aarch64-apple-darwin", (
        f"Apple Silicon must return 'aarch64-apple-darwin' (got '{triple}'). "
        "Tauri's externalBin resolver appends the Rust target triple to the "
        "sidecar base name; 'arm64-apple-darwin' would NOT match the "
        "externalBin binary 'python-sidecar-aarch64-apple-darwin'."
    )


def test_target_triple_intel_macos_returns_x86_64(monkeypatch):
    """Intel macOS (platform.machine() == 'x86_64') must return 'x86_64-apple-darwin'."""
    import platform as _platform

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(_platform, "machine", lambda: "x86_64")
    triple = prewarm_resolver._target_triple()
    assert triple == "x86_64-apple-darwin", f"Intel macOS must return 'x86_64-apple-darwin' (got '{triple}')"


def test_target_triple_macos_unknown_arch_falls_back_to_x86_64(monkeypatch):
    """An unknown macOS arch string falls back to x86_64 (safe default for Intel)."""
    import platform as _platform

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(_platform, "machine", lambda: "ppc")
    triple = prewarm_resolver._target_triple()
    assert triple == "x86_64-apple-darwin", (
        f"Unknown macOS arch should fall back to 'x86_64-apple-darwin' (got '{triple}')"
    )


def test_target_triple_linux_aarch64(monkeypatch):
    """Linux ARM64: platform.machine() returns 'aarch64' (already the Rust arch name)."""
    import platform as _platform

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform, "machine", lambda: "aarch64")
    triple = prewarm_resolver._target_triple()
    assert triple == "aarch64-unknown-linux-gnu"


def test_target_triple_linux_x86_64(monkeypatch):
    """Linux x86_64: platform.machine() returns 'x86_64'."""
    import platform as _platform

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform, "machine", lambda: "x86_64")
    triple = prewarm_resolver._target_triple()
    assert triple == "x86_64-unknown-linux-gnu"


def test_target_triple_windows_aarch64(monkeypatch):
    """Windows 11 ARM: platform.machine() returns 'ARM64' → 'aarch64-pc-windows-msvc'.

    ADR-0020 §4.1 explicitly lists aarch64-pc-windows-msvc as a supported
    target triple. The previous implementation using sys.maxsize never
    returned aarch64 on Windows ARM.
    """
    import platform as _platform

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(_platform, "machine", lambda: "ARM64")
    triple = prewarm_resolver._target_triple()
    assert triple == "aarch64-pc-windows-msvc"


def test_target_triple_windows_x86_64(monkeypatch):
    """Windows x86_64: platform.machine() returns 'AMD64' → 'x86_64-pc-windows-msvc'."""
    import platform as _platform

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(_platform, "machine", lambda: "AMD64")
    triple = prewarm_resolver._target_triple()
    assert triple == "x86_64-pc-windows-msvc"


@pytest.mark.skipif(sys.platform != "linux", reason="linux-only exe suffix")
def test_exe_suffix_correct_for_platform_linux():
    """Linux gets no suffix.

    Replaces the EC-26 silent ``if sys.platform == "win32":`` guard with
    an explicit per-platform test so non-Linux runs report SKIP (not
    silent PASS).
    """
    suffix = prewarm_resolver._exe_suffix()
    assert suffix == ""


@pytest.mark.skipif(sys.platform != "win32", reason="windows-only exe suffix")
def test_exe_suffix_correct_for_platform_windows():
    """Windows gets '.exe'."""
    suffix = prewarm_resolver._exe_suffix()
    assert suffix == ".exe"


@pytest.mark.skipif(sys.platform != "darwin", reason="macos-only exe suffix")
def test_exe_suffix_correct_for_platform_macos():
    """macOS gets no suffix."""
    suffix = prewarm_resolver._exe_suffix()
    assert suffix == ""


def test_candidate_paths_includes_env_override_first(monkeypatch, tmp_path):
    """VOICE_TYPER_PREWARM_EXE is the first candidate when set."""
    monkeypatch.setenv("VOICE_TYPER_PREWARM_EXE", str(tmp_path / "prewarm.exe"))
    candidates = prewarm_resolver._candidate_paths()
    assert len(candidates) > 0
    assert candidates[0] == Path(os.environ["VOICE_TYPER_PREWARM_EXE"])
