"""Regression tests for ``voice_typer.server.native_hotkeys.binary_path``.

per-arch native binaries. This test pins the new
``(platform, machine)`` → binary-name lookup table and the legacy
non-arch-suffixed fallback so:

1. Each supported ``(platform, machine)`` combination returns the
   correct arch-suffixed binary name.
2. Aliases (``amd64`` ↔ ``x86_64``, ``arm64`` ↔ ``aarch64``) collapse
   to the canonical arch token via :func:`_normalize_machine`.
3. macOS returns the universal ``macos-key-listener`` (no arch suffix)
   for both ``x86_64`` and ``arm64`` machines.
4. The legacy non-arch-suffixed name is appended as a fallback so
   existing Tauri bundles (which still ship ``linux-key-listener``,
   ``windows-key-listener.exe``, ``macos-key-listener`` under the
   keep working during the ``tauri.conf.json``
   transition (owned by IMPL-4 / the primary agent).
5. Unknown platforms (e.g. ``freebsd``) return ``None`` from
   :func:`get_native_binary_path` and an empty candidate-name list
   from :func:`_candidate_binary_names`.
6. End-to-end: ``get_native_binary_path()`` finds the arch-suffixed
   binary in ``VOICE_TYPER_NATIVE_DIR`` when present, and falls back
   to the legacy name when only the legacy name is shipped.
"""

from __future__ import annotations

import platform as _platform_module
import sys
from pathlib import Path

import pytest

# Import lazily inside tests so monkeypatch of sys.platform /
# platform.machine takes effect on each call.
from voice_typer.server.native_hotkeys import binary_path

# ─── _normalize_machine ─────────────────────────────────────────────────────


class TestNormalizeMachine:
    """Verify ``_normalize_machine`` collapses arch aliases."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("x86_64", "x86_64"),
            ("amd64", "x86_64"),
            ("AMD64", "x86_64"),
            ("X86_64", "x86_64"),
            ("aarch64", "aarch64"),
            ("arm64", "aarch64"),
            ("ARM64", "aarch64"),
            ("AARCH64", "aarch64"),
            ("i386", "i686"),
            ("i686", "i686"),
            ("x86", "i686"),
            # Empty / None → empty string (no crash).
            ("", ""),
            (None, ""),
        ],
    )
    def test_alias_collapse(self, raw, expected):
        assert binary_path._normalize_machine(raw) == expected

    def test_unknown_passes_through_lowercased(self):
        # An arch we don't recognize is returned lowercased so the
        # _BINARY_NAMES lookup just misses (rather than raising).
        assert binary_path._normalize_machine("RISC-V") == "risc-v"


# ─── _BINARY_NAMES table invariants ─────────────────────────────────────────


class TestBinaryNamesTable:
    """Pin the per-(platform, machine) binary filename map."""

    def test_linux_x86_64_uses_arch_suffix(self):
        assert binary_path._BINARY_NAMES[("linux", "x86_64")] == "linux-key-listener-x86_64"

    def test_linux_aarch64_uses_arch_suffix(self):
        assert binary_path._BINARY_NAMES[("linux", "aarch64")] == "linux-key-listener-aarch64"

    def test_windows_x86_64_uses_arch_suffix_with_exe(self):
        assert binary_path._BINARY_NAMES[("win32", "x86_64")] == "windows-key-listener-x86_64.exe"

    def test_windows_aarch64_uses_arch_suffix_with_exe(self):
        assert binary_path._BINARY_NAMES[("win32", "aarch64")] == "windows-key-listener-aarch64.exe"

    def test_macos_x86_64_uses_universal_name(self):
        # macOS ships a single universal binary (no arch suffix).
        assert binary_path._BINARY_NAMES[("darwin", "x86_64")] == "macos-key-listener"

    def test_macos_arm64_uses_universal_name(self):
        assert binary_path._BINARY_NAMES[("darwin", "arm64")] == "macos-key-listener"

    def test_macos_aarch64_alias_also_resolves(self):
        # ``aarch64`` is the Linux/Windows spelling; macOS hosts report
        # ``arm64``. But the table also accepts ``aarch64`` for parity.
        assert binary_path._BINARY_NAMES[("darwin", "aarch64")] == "macos-key-listener"

    def test_windows_amd64_alias_present(self):
        assert binary_path._BINARY_NAMES[("win32", "amd64")] == "windows-key-listener-x86_64.exe"

    def test_linux_amd64_alias_present(self):
        assert binary_path._BINARY_NAMES[("linux", "amd64")] == "linux-key-listener-x86_64"

    def test_linux_arm64_alias_present(self):
        assert binary_path._BINARY_NAMES[("linux", "arm64")] == "linux-key-listener-aarch64"

    def test_windows_arm64_alias_present(self):
        assert binary_path._BINARY_NAMES[("win32", "arm64")] == "windows-key-listener-aarch64.exe"


# ─── _LEGACY_BINARY_NAMES table ─────────────────────────────────────────────


class TestLegacyBinaryNamesTable:
    """Pin the fallback names (used during tauri.conf.json transition)."""

    def test_linux_legacy_is_unsuffixed(self):
        assert binary_path._LEGACY_BINARY_NAMES["linux"] == "linux-key-listener"

    def test_windows_legacy_is_unsuffixed_with_exe(self):
        assert binary_path._LEGACY_BINARY_NAMES["win32"] == "windows-key-listener.exe"

    def test_macos_legacy_matches_universal_name(self):
        # macOS legacy and primary are the same (universal binary) —
        # dedup in _candidate_binary_names should yield a single entry.
        assert binary_path._LEGACY_BINARY_NAMES["darwin"] == "macos-key-listener"


# ─── _candidate_binary_names ────────────────────────────────────────────────


class TestCandidateBinaryNames:
    """Verify the candidate-name list (arch-suffixed first, legacy fallback)."""

    def test_linux_x86_64_prefers_arch_suffix_then_legacy(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(_platform_module, "machine", lambda: "x86_64")
        names = binary_path._candidate_binary_names()
        assert names[0] == "linux-key-listener-x86_64"
        assert names[1] == "linux-key-listener"
        assert len(names) == 2

    def test_linux_aarch64_prefers_arch_suffix_then_legacy(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(_platform_module, "machine", lambda: "aarch64")
        names = binary_path._candidate_binary_names()
        assert names[0] == "linux-key-listener-aarch64"
        assert names[1] == "linux-key-listener"

    def test_windows_x86_64_prefers_arch_suffix_then_legacy(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(_platform_module, "machine", lambda: "AMD64")
        names = binary_path._candidate_binary_names()
        assert names[0] == "windows-key-listener-x86_64.exe"
        assert names[1] == "windows-key-listener.exe"

    def test_windows_aarch64_prefers_arch_suffix_then_legacy(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(_platform_module, "machine", lambda: "ARM64")
        names = binary_path._candidate_binary_names()
        assert names[0] == "windows-key-listener-aarch64.exe"
        assert names[1] == "windows-key-listener.exe"

    def test_macos_x86_64_dedups_to_single_universal_name(self, monkeypatch):
        # macOS primary and legacy are both "macos-key-listener" → dedup.
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(_platform_module, "machine", lambda: "x86_64")
        names = binary_path._candidate_binary_names()
        assert names == ["macos-key-listener"]

    def test_macos_arm64_dedups_to_single_universal_name(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(_platform_module, "machine", lambda: "arm64")
        names = binary_path._candidate_binary_names()
        assert names == ["macos-key-listener"]

    def test_unknown_platform_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "freebsd")
        monkeypatch.setattr(_platform_module, "machine", lambda: "x86_64")
        assert binary_path._candidate_binary_names() == []

    def test_unknown_arch_on_known_platform_falls_back_to_legacy(self, monkeypatch):
        # If platform.machine() returns something exotic on a known
        # platform, the arch-suffixed lookup misses but the legacy
        # fallback still applies so existing bundles keep working.
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(_platform_module, "machine", lambda: "riscv64")
        names = binary_path._candidate_binary_names()
        assert names == ["linux-key-listener"]


# ─── get_native_binary_path — happy path (arch-suffixed binary) ─────────────


class TestGetNativeBinaryPathArchSuffix:
    """Verify get_native_binary_path finds the arch-suffixed binary."""

    def test_linux_x86_64_finds_arch_suffix_in_native_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(_platform_module, "machine", lambda: "x86_64")
        native_dir = tmp_path / "native"
        native_dir.mkdir()
        binary = native_dir / "linux-key-listener-x86_64"
        binary.write_text("dummy")

        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = binary_path.get_native_binary_path()
        assert result is not None
        assert result.name == "linux-key-listener-x86_64"
        assert result.parent == native_dir

    def test_linux_aarch64_finds_arch_suffix_in_native_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(_platform_module, "machine", lambda: "aarch64")
        native_dir = tmp_path / "native"
        native_dir.mkdir()
        binary = native_dir / "linux-key-listener-aarch64"
        binary.write_text("dummy")

        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = binary_path.get_native_binary_path()
        assert result is not None
        assert result.name == "linux-key-listener-aarch64"

    def test_windows_x86_64_finds_arch_suffix_in_native_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(_platform_module, "machine", lambda: "AMD64")
        native_dir = tmp_path / "native"
        native_dir.mkdir()
        binary = native_dir / "windows-key-listener-x86_64.exe"
        binary.write_text("dummy")

        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = binary_path.get_native_binary_path()
        assert result is not None
        assert result.name == "windows-key-listener-x86_64.exe"

    def test_windows_aarch64_finds_arch_suffix_in_native_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(_platform_module, "machine", lambda: "ARM64")
        native_dir = tmp_path / "native"
        native_dir.mkdir()
        binary = native_dir / "windows-key-listener-aarch64.exe"
        binary.write_text("dummy")

        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = binary_path.get_native_binary_path()
        assert result is not None
        assert result.name == "windows-key-listener-aarch64.exe"

    def test_macos_universal_finds_in_native_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(_platform_module, "machine", lambda: "arm64")
        native_dir = tmp_path / "native"
        native_dir.mkdir()
        binary = native_dir / "macos-key-listener"
        binary.write_text("dummy")

        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = binary_path.get_native_binary_path()
        assert result is not None
        assert result.name == "macos-key-listener"


# ─── get_native_binary_path — legacy fallback ───────────────────────────────


class TestGetNativeBinaryPathLegacyFallback:
    """Verify the legacy non-arch-suffixed name is used as a fallback.

    This pins the transition behavior: existing bundles that
    still ship ``linux-key-listener`` (no arch suffix) keep working
    until IMPL-4 / the primary agent updates tauri.conf.json to ship
    the arch-suffixed names exclusively.
    """

    def test_linux_falls_back_to_legacy_when_arch_suffix_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(_platform_module, "machine", lambda: "x86_64")
        native_dir = tmp_path / "native"
        native_dir.mkdir()
        # Only the legacy name is shipped (pre- bundle).
        legacy = native_dir / "linux-key-listener"
        legacy.write_text("dummy")

        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = binary_path.get_native_binary_path()
        assert result is not None
        assert result.name == "linux-key-listener"

    def test_windows_falls_back_to_legacy_when_arch_suffix_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(_platform_module, "machine", lambda: "AMD64")
        native_dir = tmp_path / "native"
        native_dir.mkdir()
        legacy = native_dir / "windows-key-listener.exe"
        legacy.write_text("dummy")

        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = binary_path.get_native_binary_path()
        assert result is not None
        assert result.name == "windows-key-listener.exe"

    def test_arch_suffix_preferred_over_legacy_when_both_present(self, monkeypatch, tmp_path):
        # When both the arch-suffixed and legacy binaries are shipped,
        # the arch-suffixed one wins ( preferred).
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(_platform_module, "machine", lambda: "x86_64")
        native_dir = tmp_path / "native"
        native_dir.mkdir()
        (native_dir / "linux-key-listener").write_text("legacy")
        (native_dir / "linux-key-listener-x86_64").write_text("arch-suffix")

        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = binary_path.get_native_binary_path()
        assert result is not None
        assert result.name == "linux-key-listener-x86_64"


# ─── get_native_binary_path — env var & unknown platform ────────────────────


class TestGetNativeBinaryPathEnvAndUnknown:
    """Verify env var precedence and unknown-platform handling."""

    def test_env_var_override_takes_precedence(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(_platform_module, "machine", lambda: "x86_64")
        single = tmp_path / "custom-listener"
        single.write_text("dummy")

        native_dir = tmp_path / "native"
        native_dir.mkdir()
        (native_dir / "linux-key-listener-x86_64").write_text("dummy")

        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", str(single))
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = binary_path.get_native_binary_path()
        assert result == single

    def test_unknown_platform_returns_none(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "freebsd")
        monkeypatch.setattr(_platform_module, "machine", lambda: "x86_64")
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        assert binary_path.get_native_binary_path() is None

    def test_returns_none_when_no_binary_anywhere(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(_platform_module, "machine", lambda: "x86_64")
        # Empty native dir → no binary found in any candidate location.
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(empty_dir))
        # Also patch out dev-mode / PyInstaller lookups to avoid
        # accidentally finding a binary from the real source tree.
        monkeypatch.setattr(
            binary_path,
            "_candidate_binary_names",
            lambda: ["linux-key-listener-x86_64", "linux-key-listener"],
        )
        # Override Path.is_file to always return False for our probe
        # paths — this guarantees None even if the real source tree
        # has a compiled binary from a prior test run.
        real_is_file = Path.is_file

        def fake_is_file(self):
            # Only allow the empty_dir probes (which will return False
            # anyway since the dir is empty) — reject everything else
            # so the dev-mode + PyInstaller fallbacks all miss.
            if str(self).startswith(str(empty_dir)):
                return real_is_file(self)
            return False

        monkeypatch.setattr(Path, "is_file", fake_is_file)
        assert binary_path.get_native_binary_path() is None


# ─── Dev-mode lookup uses arch-suffixed name ─────────────────────────────────


class TestDevModeArchSuffixLookup:
    """Verify the dev-mode (source-tree) lookup uses the arch-suffixed name."""

    def test_dev_mode_finds_arch_suffix_in_source_tree(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(_platform_module, "machine", lambda: "x86_64")
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)

        # Construct the expected dev-mode path the same way
        # binary_path.get_native_binary_path does it.
        module_dir = Path(binary_path.__file__).resolve().parent.parent / "native"
        expected = module_dir / "linux-key-listener-x86_64"

        real_is_file = Path.is_file

        def fake_is_file(self):
            if self == expected:
                return True
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", fake_is_file)

        result = binary_path.get_native_binary_path()
        assert result is not None
        assert result == expected
