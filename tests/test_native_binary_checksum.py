"""Regression tests for the native binary SHA-256 checksum verification"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from voice_typer.server.native_hotkeys.binary_path import (
    _MANIFEST_PATH,
    _is_trusted_path_override,
    _path_matches_env_override,
    get_expected_sha256,
    load_binary_manifest,
    verify_native_binary,
    verify_native_binary_or_skip,
)


@pytest.fixture
def clean_env(monkeypatch):
    """Remove trusted-override env vars so verification actually runs."""
    monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
    monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
    monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)


@pytest.fixture
def fake_binary(tmp_path):
    """Create a fake binary file with known content and return its path."""
    path = tmp_path / "fake-key-listener"
    path.write_bytes(b"#!/bin/sh\necho READY\n")
    return path


@pytest.fixture
def tampered_binary(tmp_path):
    """Create a fake binary file with DIFFERENT content (a 'tampered' binary)."""
    path = tmp_path / "tampered-key-listener"
    path.write_bytes(b"#!/bin/sh\n# MALICIOUS KEYLOGGER\n")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestVerifyNativeBinary:
    """Tests for the pure ``verify_native_binary(path, expected_sha256)`` function."""

    def test_accepts_matching_hash(self, fake_binary, caplog):
        """A binary whose SHA-256 matches the expected value is accepted."""
        expected = _sha256(fake_binary)
        with caplog.at_level("DEBUG"):
            assert verify_native_binary(fake_binary, expected) is True

    def test_rejects_tampered_binary(self, fake_binary, tampered_binary, caplog):
        """A tampered binary (different content → different hash) is rejected."""
        expected = _sha256(fake_binary)  # hash of the LEGITIMATE binary
        with caplog.at_level("ERROR"):
            assert verify_native_binary(tampered_binary, expected) is False
        # The ERROR log must mention "CHECKSUM MISMATCH" and both hashes
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(error_records) >= 1, "Expected an ERROR log for checksum mismatch"
        msg = error_records[0].getMessage()
        assert "CHECKSUM MISMATCH" in msg
        assert expected in msg  # expected hash logged
        assert _sha256(tampered_binary) in msg  # actual hash logged

    def test_hash_comparison_is_case_insensitive(self, fake_binary):
        """The hash comparison is case-insensitive (hex digests may be upper or lower)."""
        expected_lower = _sha256(fake_binary).lower()
        expected_upper = expected_lower.upper()
        assert verify_native_binary(fake_binary, expected_lower) is True
        assert verify_native_binary(fake_binary, expected_upper) is True

    def test_returns_false_on_read_error(self, tmp_path):
        """If the binary can't be read (e.g. permission denied), return False."""
        # Use a path that doesn't exist, read_bytes() raises FileNotFoundError
        missing = tmp_path / "does-not-exist"
        assert verify_native_binary(missing, "0" * 64) is False

    def test_accepts_matching_hash_with_whitespace(self, fake_binary):
        """Leading/trailing whitespace in the expected hash is stripped."""
        expected = _sha256(fake_binary)
        assert verify_native_binary(fake_binary, f"  {expected}  ") is True


class TestGetExpectedSha256:
    """Tests for ``get_expected_sha256(binary_name)``, manifest lookup."""

    def test_returns_none_for_unknown_binary(self):
        """An unknown binary name (not in the manifest) returns None."""
        assert get_expected_sha256("nonexistent-binary-name-xyz") is None

    def test_legacy_non_suffixed_names_resolve_via_equivalence(self):
        """same sha256 as their arch-suffixed x86_64 counterparts (when"""
        # Linux x86_64: both forms populated with the same sha256.
        legacy_linux = get_expected_sha256("linux-key-listener")
        arch_linux = get_expected_sha256("linux-key-listener-x86_64")
        assert arch_linux is not None, "Manifest must have a non-empty sha256 for linux-key-listener-x86_64"
        assert legacy_linux == arch_linux, (
            f"FR-19: legacy 'linux-key-listener' sha256 ({legacy_linux}) must "
            f"equal arch-suffixed 'linux-key-listener-x86_64' sha256 ({arch_linux})"
        )
        # Windows x86_64:  pre-populated both forms with the same
        legacy_windows = get_expected_sha256("windows-key-listener.exe")
        arch_windows = get_expected_sha256("windows-key-listener-x86_64.exe")
        assert arch_windows is not None, (
            "Manifest must have a non-empty sha256 for "
            "windows-key-listener-x86_64.exe (computed from the committed "
            "windows-key-listener.exe binary)."
        )
        assert legacy_windows == arch_windows, (
            f"FR-19: legacy 'windows-key-listener.exe' sha256 "
            f"({legacy_windows}) must equal arch-suffixed "
            f"'windows-key-listener-x86_64.exe' sha256 ({arch_windows})."
        )

    def test_returns_none_for_empty_sha256_entry(self):
        """A manifest entry with an empty sha256 field returns None."""
        # The shipped manifest has empty sha256 for these arch-suffixed
        assert get_expected_sha256("macos-key-listener") is None
        assert get_expected_sha256("windows-key-listener-aarch64.exe") is None
        assert get_expected_sha256("linux-key-listener-aarch64") is None

    def test_windows_x86_64_sha256_is_pre_populated(self):
        """(Critical): the Windows x86_64 sha256 MUST be pre-populated"""
        expected = "a7fef26377e9ef7c53b9675651217d13d5fbc61c7c3e2c6cd204b9178e9a14c1"
        # Direct (arch-suffixed) entry.
        arch_sha = get_expected_sha256("windows-key-listener-x86_64.exe")
        assert arch_sha == expected, (
            f"'windows-key-listener-x86_64.exe' sha256 must be "
            f"{expected} (computed from the committed Windows binary), "
            f"got {arch_sha!r}."
        )
        # Legacy alias entry (backward-compat flat field).
        legacy_sha = get_expected_sha256("windows-key-listener.exe")
        assert legacy_sha == expected, (
            f"legacy 'windows-key-listener.exe' sha256 must be "
            f"{expected} (same binary as arch-suffixed), got "
            f"{legacy_sha!r}."
        )

    def test_windows_x86_64_sha256_matches_actual_binary(self):
        """stale manifest if the Windows binary is rebuilt without"""
        native_dir = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "native"
        real_binary = native_dir / "windows-key-listener.exe"
        if not real_binary.is_file():
            pytest.skip("windows-key-listener.exe not committed in this tree")
        expected = get_expected_sha256("windows-key-listener-x86_64.exe")
        assert expected is not None, "Manifest must have a non-empty sha256 for windows-key-listener-x86_64.exe"
        actual = hashlib.sha256(real_binary.read_bytes()).hexdigest()
        assert actual == expected, (
            f"Manifest sha256 for windows-key-listener-x86_64.exe is STALE: "
            f"manifest says {expected}, actual binary is {actual}. "
            f"Run scripts/build/update_native_manifests.py to refresh."
        )

    def test_returns_hash_for_known_binary(self):
        """A manifest entry with a non-empty sha256 returns the hash (lowercased)."""
        sha = get_expected_sha256("linux-key-listener-x86_64")
        assert sha is not None
        assert len(sha) == 64  # SHA-256 hex digest length
        assert sha == sha.lower()  # normalized to lowercase

    def test_returns_none_when_manifest_missing(self, monkeypatch, tmp_path):
        """If the manifest file doesn't exist, return None (skip verification)."""
        # Point _MANIFEST_PATH at a non-existent file by monkeypatching
        from voice_typer.server.native_hotkeys import binary_path as bp

        monkeypatch.setattr(bp, "_MANIFEST_PATH", tmp_path / "no-such-manifest.json")
        assert get_expected_sha256("linux-key-listener-x86_64") is None

    def test_returns_none_when_manifest_malformed(self, monkeypatch, tmp_path):
        """If the manifest is malformed JSON, return None (skip verification)."""
        from voice_typer.server.native_hotkeys import binary_path as bp

        bad = tmp_path / "bad-manifest.json"
        bad.write_text("{ this is not valid JSON")
        monkeypatch.setattr(bp, "_MANIFEST_PATH", bad)
        assert get_expected_sha256("linux-key-listener-x86_64") is None


# _is_trusted_path_override ( + ) ────────────────────────


class TestIsTrustedPathOverride:
    """Tests for the env-var trusted-override check."""

    def test_returns_false_when_no_env_vars(self, monkeypatch):
        """No env vars set → not a trusted override → verification runs."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        assert _is_trusted_path_override() is False

    def test_returns_false_when_native_binary_set_without_trust(self, monkeypatch):
        """G4-L-09: ``VOICE_TYPER_NATIVE_BINARY`` alone is NOT enough —"""
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", "/custom/path/binary")
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        assert _is_trusted_path_override() is False

    def test_returns_false_when_native_dir_set_without_trust(self, monkeypatch):
        """G4-L-09: ``VOICE_TYPER_NATIVE_DIR`` alone is NOT enough."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", "/custom/dir")
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        assert _is_trusted_path_override() is False

    def test_returns_false_when_trust_set_without_path_env(self, monkeypatch):
        """G4-L-09: ``VOICE_TYPER_NATIVE_TRUST=1`` alone is NOT enough —"""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "1")
        assert _is_trusted_path_override() is False

    def test_returns_true_when_trust_and_native_binary_set(self, monkeypatch):
        """G4-L-09: ``VOICE_TYPER_NATIVE_TRUST=1`` +"""
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", "/custom/path/binary")
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "1")
        assert _is_trusted_path_override() is True

    def test_returns_true_when_trust_and_native_dir_set(self, monkeypatch):
        """G4-L-09: ``VOICE_TYPER_NATIVE_TRUST=1`` +"""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", "/custom/dir")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "1")
        assert _is_trusted_path_override() is True

    def test_returns_true_when_trust_and_both_path_envs_set(self, monkeypatch):
        """G4-L-09: ``VOICE_TYPER_NATIVE_TRUST=1`` + both path envs → trusted."""
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", "/a/b")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", "/c/d")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "1")
        assert _is_trusted_path_override() is True

    def test_returns_false_when_trust_set_to_other_value(self, monkeypatch):
        """G4-L-09: ``VOICE_TYPER_NATIVE_TRUST`` must be exactly ``\"1\"``."""
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", "/a/b")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "true")
        assert _is_trusted_path_override() is False

    def test_returns_false_when_trust_set_to_empty(self, monkeypatch):
        """G4-L-09: an empty ``VOICE_TYPER_NATIVE_TRUST`` is treated as unset."""
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", "/a/b")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "")
        assert _is_trusted_path_override() is False


# _path_matches_env_override  ─────────────────────────────────


class TestPathMatchesEnvOverride:
    """G4-H-34: ``verify_native_binary_or_skip`` only skips verification"""

    def test_matches_when_path_equals_env_binary(self, monkeypatch, tmp_path):
        """Exact match with ``VOICE_TYPER_NATIVE_BINARY``."""
        binary = tmp_path / "custom-listener"
        binary.write_text("x")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", str(binary))
        assert _path_matches_env_override(binary) is True

    def test_matches_when_path_under_env_dir(self, monkeypatch, tmp_path):
        """Discovered binary lives under ``VOICE_TYPER_NATIVE_DIR``."""
        native_dir = tmp_path / "native"
        native_dir.mkdir()
        binary = native_dir / "linux-key-listener-x86_64"
        binary.write_text("x")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))
        assert _path_matches_env_override(binary) is True

    def test_does_not_match_when_path_outside_env_dir(self, monkeypatch, tmp_path):
        """G4-H-34: setting ``VOICE_TYPER_NATIVE_DIR`` does NOT bypass"""
        env_dir = tmp_path / "env-specified-dir"
        env_dir.mkdir()
        discovered = tmp_path / "discovered-elsewhere" / "linux-key-listener-x86_64"
        discovered.parent.mkdir()
        discovered.write_text("x")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(env_dir))
        assert _path_matches_env_override(discovered) is False

    def test_does_not_match_when_no_env_vars_set(self, monkeypatch, tmp_path):
        """No env vars set → no match → verification runs normally."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        binary = tmp_path / "any-binary"
        binary.write_text("x")
        assert _path_matches_env_override(binary) is False

    def test_does_not_match_sibling_directory_with_similar_prefix(self, monkeypatch, tmp_path):
        """G4-H-34: path-prefix-based check must NOT be fooled by sibling"""
        env_dir = tmp_path / "native"
        env_dir.mkdir()
        sibling_dir = tmp_path / "native2"  # common string prefix
        sibling_dir.mkdir()
        binary = sibling_dir / "linux-key-listener-x86_64"
        binary.write_text("x")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(env_dir))
        assert _path_matches_env_override(binary) is False


class TestVerifyNativeBinaryOrSkip:
    """Tests for the composed helper used by the factory."""

    def test_skips_when_trusted_override_set_and_path_matches(self, monkeypatch, fake_binary):
        """G4-L-09 + G4-H-34: when ``VOICE_TYPER_NATIVE_TRUST=1`` is set"""
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(fake_binary.parent))
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "1")
        # Even with a 'tampered' binary (hash won't match the manifest),
        assert verify_native_binary_or_skip(fake_binary) is True

    def test_does_not_skip_when_trust_set_but_path_does_not_match(self, monkeypatch, fake_binary, tmp_path):
        """G4-H-34: setting ``VOICE_TYPER_NATIVE_TRUST=1`` +"""
        env_dir = tmp_path / "env-specified"
        env_dir.mkdir()
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(env_dir))
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "1")
        assert verify_native_binary_or_skip(fake_binary) is False

    def test_does_not_skip_when_path_matches_but_trust_unset(self, monkeypatch, fake_binary):
        """G4-L-09: setting the path env var alone (without TRUST=1) is"""
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(fake_binary.parent))
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        assert verify_native_binary_or_skip(fake_binary) is False

    def test_fails_closed_when_no_manifest_entry(self, monkeypatch, fake_binary):
        """G4-L-11 + when the manifest has no entry for the binary"""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        assert verify_native_binary_or_skip(fake_binary) is False

    def test_fails_closed_logs_error(self, monkeypatch, fake_binary, caplog):
        """G4-L-11 + the fail-closed branch logs an ERROR so"""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        with caplog.at_level("ERROR"):
            assert verify_native_binary_or_skip(fake_binary) is False
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(error_records) >= 1, "Expected an ERROR log for fail-closed"
        msg = error_records[0].getMessage()
        assert "FAIL CLOSED" in msg
        assert "fake-key-listener" in msg

    def test_rejects_when_checksum_mismatches(self, monkeypatch, tampered_binary):
        """When the manifest entry exists and the checksum doesn't match, reject."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        # Rename the tampered binary to the arch-suffixed name
        named = tampered_binary.parent / "linux-key-listener-x86_64"
        tampered_binary.rename(named)
        # The manifest's linux-key-listener-x86_64 sha256 is the hash
        assert verify_native_binary_or_skip(named) is False

    def test_accepts_when_checksum_matches(self, monkeypatch, tmp_path):
        """When the manifest entry exists and the checksum matches, accept."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        # Create a binary whose content matches the manifest's sha256
        real_binary = (
            Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "native" / "linux-key-listener-x86_64"
        )
        if not real_binary.is_file():
            pytest.skip("linux-key-listener-x86_64 binary not built in this tree")
        named = tmp_path / "linux-key-listener-x86_64"
        named.write_bytes(real_binary.read_bytes())
        assert verify_native_binary_or_skip(named) is True


# Parametrized manifest entry coverage  ──────────────────────


class TestManifestEntryParametrized:
    """G4-L-11: a parametrized test that runs ``verify_native_binary_or_skip``"""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        """Ensure no trusted-override env vars leak into the parametrized tests."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)

    @pytest.mark.parametrize(
        "entry_name",
        [
            "linux-key-listener-x86_64",
            "linux-key-listener-aarch64",
            "windows-key-listener-x86_64.exe",
            "windows-key-listener-aarch64.exe",
            "macos-key-listener",
            "linux-key-listener",
            "windows-key-listener.exe",
        ],
    )
    def test_each_manifest_entry_is_handled(self, entry_name, tmp_path):
        """manifest entry is created with bogus content; the function"""
        # Create a temp binary with the manifest entry name.
        named = tmp_path / entry_name
        named.write_bytes(b"#!bogus content for parametrized test\n")
        # The function must NOT return True, there's no trusted override
        result = verify_native_binary_or_skip(named)
        assert result is False, (
            f"verify_native_binary_or_skip({entry_name}) returned {result}; "
            f"expected False (fail-closed or checksum-mismatch) for a bogus binary "
            f"without a trusted-override env var."
        )


class TestFactoryChecksumGate:
    """``is_native_backend_available`` enforce the checksum gate."""

    def test_create_native_backend_returns_none_for_tampered_binary(self, monkeypatch, tampered_binary, clean_env):
        """Factory returns None (→ legacy fallback) when the binary is tampered."""
        from voice_typer.server.native_hotkeys import factory as factory_mod

        named = tampered_binary.parent / "linux-key-listener-x86_64"
        tampered_binary.rename(named)

        # Force the factory to think it's on Linux and the binary is
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(factory_mod, "is_linux", lambda: True)
        monkeypatch.setattr(factory_mod, "is_macos", lambda: False)
        monkeypatch.setattr(factory_mod, "is_windows", lambda: False)
        monkeypatch.setattr(
            factory_mod,
            "get_native_binary_path",
            lambda: named,
        )

        backend = factory_mod.create_native_backend("<f8>")
        assert backend is None, (
            "create_native_backend must return None (→ legacy fallback) "
            "when the discovered binary's checksum doesn't match the manifest"
        )

    def test_is_native_backend_available_returns_false_for_tampered_binary(
        self, monkeypatch, tampered_binary, clean_env
    ):
        """``is_native_backend_available`` returns False for a tampered binary."""
        from voice_typer.server.native_hotkeys import factory as factory_mod

        named = tampered_binary.parent / "linux-key-listener-x86_64"
        tampered_binary.rename(named)

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(factory_mod, "is_linux", lambda: True)
        monkeypatch.setattr(factory_mod, "is_macos", lambda: False)
        monkeypatch.setattr(factory_mod, "is_windows", lambda: False)
        monkeypatch.setattr(
            factory_mod,
            "get_native_binary_path",
            lambda: named,
        )

        assert factory_mod.is_native_backend_available() is False

    def test_create_native_backend_returns_backend_when_checksum_ok(self, monkeypatch, tmp_path, clean_env):
        """Factory returns a real backend when the checksum matches the manifest."""
        from voice_typer.server.native_hotkeys import factory as factory_mod

        real_binary = (
            Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "native" / "linux-key-listener-x86_64"
        )
        if not real_binary.is_file():
            pytest.skip("linux-key-listener-x86_64 binary not built in this tree")

        named = tmp_path / "linux-key-listener-x86_64"
        named.write_bytes(real_binary.read_bytes())

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(factory_mod, "is_linux", lambda: True)
        monkeypatch.setattr(factory_mod, "is_macos", lambda: False)
        monkeypatch.setattr(factory_mod, "is_windows", lambda: False)
        monkeypatch.setattr(
            factory_mod,
            "get_native_binary_path",
            lambda: named,
        )

        backend = factory_mod.create_native_backend("<f8>")
        assert backend is not None, "create_native_backend must return a backend when the checksum matches"
        assert type(backend).__name__ == "LinuxEvdevHotkey"

    def test_create_native_backend_skips_verification_with_trusted_override(self, monkeypatch, tampered_binary):
        """factory accepts the binary without checksum verification."""
        from voice_typer.server.native_hotkeys import factory as factory_mod

        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(tampered_binary.parent))
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "1")
        named = tampered_binary.parent / "linux-key-listener-x86_64"
        tampered_binary.rename(named)

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(factory_mod, "is_linux", lambda: True)
        monkeypatch.setattr(factory_mod, "is_macos", lambda: False)
        monkeypatch.setattr(factory_mod, "is_windows", lambda: False)
        monkeypatch.setattr(
            factory_mod,
            "get_native_binary_path",
            lambda: named,
        )

        backend = factory_mod.create_native_backend("<f8>")
        assert backend is not None, (
            "With VOICE_TYPER_NATIVE_TRUST=1 + VOICE_TYPER_NATIVE_DIR set, "
            "the factory must accept the binary without checksum verification "
            "(even if 'tampered')."
        )

    def test_create_native_backend_does_not_skip_when_trust_unset(self, monkeypatch, tampered_binary):
        """G4-L-09: setting ``VOICE_TYPER_NATIVE_DIR`` WITHOUT"""
        from voice_typer.server.native_hotkeys import factory as factory_mod

        # Set ONLY the dir env var (no trust flag).
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(tampered_binary.parent))
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        named = tampered_binary.parent / "linux-key-listener-x86_64"
        tampered_binary.rename(named)

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(factory_mod, "is_linux", lambda: True)
        monkeypatch.setattr(factory_mod, "is_macos", lambda: False)
        monkeypatch.setattr(factory_mod, "is_windows", lambda: False)
        monkeypatch.setattr(
            factory_mod,
            "get_native_binary_path",
            lambda: named,
        )

        backend = factory_mod.create_native_backend("<f8>")
        assert backend is None, (
            "Without VOICE_TYPER_NATIVE_TRUST=1, the factory must NOT skip "
            "verification, the tampered binary must be rejected."
        )


class TestManifestSanity:
    """Sanity checks on the shipped ``binaries.json`` manifest."""

    def test_manifest_file_exists(self):
        """The manifest file exists at the expected path."""
        assert _MANIFEST_PATH.is_file(), (
            f" manifest not found at {_MANIFEST_PATH}. "
            "Run scripts/build/compile_native.sh + update_native_manifests.py."
        )

    def test_manifest_is_valid_json(self):
        """The manifest is valid JSON with the expected shape."""
        manifest = load_binary_manifest()
        assert manifest is not None
        assert "version" in manifest
        assert "binaries" in manifest
        binaries = manifest["binaries"]
        assert isinstance(binaries, dict)
        for name in (
            "linux-key-listener-x86_64",
            "linux-key-listener-aarch64",
            "windows-key-listener-x86_64.exe",
            "windows-key-listener-aarch64.exe",
            "macos-key-listener",
            # legacy aliases.
            "linux-key-listener",
            "windows-key-listener.exe",
        ):
            assert name in binaries, f"Manifest missing entry for {name}"
            entry = binaries[name]
            assert isinstance(entry, dict)
            assert "sha256" in entry
            assert "version" in entry
            assert "min_proto_version" in entry

    def test_manifest_has_legacy_non_suffixed_entries_as_aliases(self):
        """FR-19: the manifest now includes BOTH arch-suffixed AND legacy"""
        manifest = load_binary_manifest()
        assert manifest is not None
        binaries = manifest["binaries"]
        # The legacy alias names must be present.
        assert "linux-key-listener" in binaries, (
            "FR-19: manifest must have legacy 'linux-key-listener' entry, "
            "compile_native.sh emits this name (not the arch-suffixed "
            "'linux-key-listener-x86_64') on Linux, so "
            "verify_native_binary_or_skip needs a manifest entry to verify it."
        )
        assert "windows-key-listener.exe" in binaries, (
            "FR-19: manifest must have legacy 'windows-key-listener.exe' entry, "
            "compile_native.sh emits this name (not the arch-suffixed "
            "'windows-key-listener-x86_64.exe') on Windows, so "
            "verify_native_binary_or_skip needs a manifest entry to verify it."
        )
        # The legacy entries must carry the SAME sha256 as their
        legacy_linux_sha = binaries["linux-key-listener"]["sha256"]
        arch_linux_sha = binaries["linux-key-listener-x86_64"]["sha256"]
        assert legacy_linux_sha == arch_linux_sha, (
            f"FR-19: legacy 'linux-key-listener' sha256 ({legacy_linux_sha}) "
            f"must equal arch-suffixed 'linux-key-listener-x86_64' sha256 "
            f"({arch_linux_sha}), they are the same binary, just different "
            f"filename conventions."
        )
        # Windows x86_64 is not built on the Linux dev host → both empty.
        legacy_windows_sha = binaries["windows-key-listener.exe"]["sha256"]
        arch_windows_sha = binaries["windows-key-listener-x86_64.exe"]["sha256"]
        assert legacy_windows_sha == arch_windows_sha, (
            f"FR-19: legacy 'windows-key-listener.exe' sha256 "
            f"({legacy_windows_sha}) must equal arch-suffixed "
            f"'windows-key-listener-x86_64.exe' sha256 ({arch_windows_sha}), "
            f"they are the same binary, just different filename conventions."
        )

    def test_linux_binary_sha256_matches_actual_binary(self):
        """The manifest's linux sha256 matches the actual on-disk binary."""
        native_dir = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "native"
        real_binary = None
        binary_name = None
        for name in ("linux-key-listener-x86_64", "linux-key-listener"):
            candidate = native_dir / name
            if candidate.is_file():
                real_binary = candidate
                binary_name = name
                break
        if real_binary is None:
            pytest.skip("no linux-key-listener binary built in this tree")
        expected = get_expected_sha256(binary_name)
        assert expected is not None, f"Manifest must have a non-empty sha256 for {binary_name}"
        actual = hashlib.sha256(real_binary.read_bytes()).hexdigest()
        assert actual == expected, (
            f"Manifest sha256 for {binary_name} is STALE: "
            f"manifest says {expected}, actual binary is {actual}. "
            f"Run scripts/build/update_native_manifests.py to refresh."
        )


class TestLegacyNameManifestLookup:
    """FR-19 regression: legacy-named binaries (``linux-key-listener``,"""

    def test_legacy_linux_name_resolves_to_same_sha_as_arch_suffix(self):
        """same sha256 as ``get_expected_sha256('linux-key-listener-x86_64')``"""
        arch_sha = get_expected_sha256("linux-key-listener-x86_64")
        legacy_sha = get_expected_sha256("linux-key-listener")
        # The arch-suffixed entry IS populated in the dev tree.
        assert arch_sha is not None, "Manifest must have a non-empty sha256 for linux-key-listener-x86_64"
        assert legacy_sha is not None, (
            "FR-19: legacy 'linux-key-listener' must resolve to a sha256 when "
            "arch-suffixed 'linux-key-listener-x86_64' is populated, otherwise "
            "verify_native_binary_or_skip fails-closed for the on-disk legacy-"
            "named binary and the native hotkey backend is disabled."
        )
        assert legacy_sha == arch_sha, (
            f"FR-19: legacy 'linux-key-listener' sha256 ({legacy_sha}) must "
            f"equal arch-suffixed 'linux-key-listener-x86_64' sha256 "
            f"({arch_sha}), they are the same binary."
        )

    def test_legacy_windows_name_resolves_same_as_arch_suffix(self):
        """FR-19: ``get_expected_sha256('windows-key-listener.exe')``"""
        arch_sha = get_expected_sha256("windows-key-listener-x86_64.exe")
        legacy_sha = get_expected_sha256("windows-key-listener.exe")
        assert arch_sha is not None, (
            "'windows-key-listener-x86_64.exe' sha256 must be "
            "non-None (pre-populated from the committed Windows binary)."
        )
        assert legacy_sha == arch_sha, (
            f"FR-19: legacy 'windows-key-listener.exe' sha256 "
            f"({legacy_sha}) must equal arch-suffixed "
            f"'windows-key-listener-x86_64.exe' sha256 ({arch_sha}), "
            f"they are the same binary."
        )

    def test_aarch64_names_do_not_fall_back_to_legacy_x86_64(self):
        """the legacy name (which is the x86_64 binary). aarch64 builds are"""
        # linux-key-listener-aarch64: empty in dev tree, must NOT fall
        assert get_expected_sha256("linux-key-listener-aarch64") is None, (
            "FR-19: 'linux-key-listener-aarch64' must NOT fall back to the "
            "legacy 'linux-key-listener' (x86_64) sha256, aarch64 is a "
            "different binary and must be verified against its own sha256."
        )
        # windows-key-listener-aarch64.exe: empty in dev tree, must NOT
        assert get_expected_sha256("windows-key-listener-aarch64.exe") is None, (
            "FR-19: 'windows-key-listener-aarch64.exe' must NOT fall back to "
            "the legacy 'windows-key-listener.exe' (x86_64) sha256, aarch64 "
            "is a different binary and must be verified against its own sha256."
        )

    def test_verify_native_binary_or_skip_accepts_on_disk_legacy_linux_binary(self, monkeypatch):
        """FR-19 end-to-end: ``verify_native_binary_or_skip`` returns"""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        real_binary = (
            Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "native" / "linux-key-listener"
        )
        if not real_binary.is_file():
            pytest.skip("linux-key-listener binary not built in this tree")
        assert verify_native_binary_or_skip(real_binary) is True, (
            "FR-19: verify_native_binary_or_skip must accept the on-disk "
            "linux-key-listener binary (legacy name), its sha256 must "
            "match the manifest's legacy alias entry. Pre-FR-19 this "
            "returned False (fail-closed) because the manifest was keyed "
            "only by the arch-suffixed name."
        )

    def test_equivalent_manifest_names_helper(self):
        """``get_expected_sha256`` uses to find equivalent manifest keys."""
        from voice_typer.server.native_hotkeys.binary_path import (
            _equivalent_manifest_names,
        )

        # Legacy name → [legacy, arch-suffixed x86_64].
        assert _equivalent_manifest_names("linux-key-listener") == [
            "linux-key-listener",
            "linux-key-listener-x86_64",
        ]
        assert _equivalent_manifest_names("windows-key-listener.exe") == [
            "windows-key-listener.exe",
            "windows-key-listener-x86_64.exe",
        ]
        # Arch-suffixed x86_64 name → [arch-suffixed, legacy].
        assert _equivalent_manifest_names("linux-key-listener-x86_64") == [
            "linux-key-listener-x86_64",
            "linux-key-listener",
        ]
        assert _equivalent_manifest_names("windows-key-listener-x86_64.exe") == [
            "windows-key-listener-x86_64.exe",
            "windows-key-listener.exe",
        ]
        assert _equivalent_manifest_names("linux-key-listener-aarch64") == [
            "linux-key-listener-aarch64",
        ]
        assert _equivalent_manifest_names("windows-key-listener-aarch64.exe") == [
            "windows-key-listener-aarch64.exe",
        ]
        assert _equivalent_manifest_names("macos-key-listener") == [
            "macos-key-listener",
        ]
        # Unknown name → single-element list (no equivalents).
        assert _equivalent_manifest_names("nonexistent-binary") == [
            "nonexistent-binary",
        ]


class TestPerArchSha256ManifestSchema:
    """(High): the legacy manifest entries (``linux-key-listener``,"""

    def test_linux_legacy_entry_has_sha256_by_arch_field(self):
        """``sha256_by_arch`` field (a dict keyed by arch)."""
        manifest = load_binary_manifest()
        assert manifest is not None
        entry = manifest["binaries"]["linux-key-listener"]
        assert "sha256_by_arch" in entry, (
            "legacy 'linux-key-listener' entry must carry a "
            "'sha256_by_arch' dict so the manifest can disambiguate the "
            "same legacy file name across x86_64 and aarch64 arches."
        )
        assert isinstance(entry["sha256_by_arch"], dict), "'linux-key-listener.sha256_by_arch' must be a dict."

    def test_windows_legacy_entry_has_sha256_by_arch_field(self):
        """the ``windows-key-listener.exe`` legacy entry MUST"""
        manifest = load_binary_manifest()
        assert manifest is not None
        entry = manifest["binaries"]["windows-key-listener.exe"]
        assert "sha256_by_arch" in entry, (
            "legacy 'windows-key-listener.exe' entry must carry a "
            "'sha256_by_arch' dict so the manifest can disambiguate the "
            "same legacy file name across x86_64 and aarch64 arches."
        )
        assert isinstance(entry["sha256_by_arch"], dict), "'windows-key-listener.exe.sha256_by_arch' must be a dict."

    def test_arch_suffixed_entries_do_not_need_sha256_by_arch(self):
        """arch-suffixed entries (``linux-key-listener-x86_64``,"""
        manifest = load_binary_manifest()
        assert manifest is not None
        for name in (
            "linux-key-listener-x86_64",
            "linux-key-listener-aarch64",
            "windows-key-listener-x86_64.exe",
            "windows-key-listener-aarch64.exe",
            "macos-key-listener",
        ):
            entry = manifest["binaries"][name]
            # Arch-suffixed entries MAY have a sha256_by_arch field
            assert "sha256" in entry, f"arch-suffixed entry '{name}' must still have the flat 'sha256' string field."

    def test_sha256_by_arch_has_x86_64_and_aarch64_keys(self):
        """each legacy entry's ``sha256_by_arch`` dict MUST"""
        manifest = load_binary_manifest()
        assert manifest is not None
        for name in ("linux-key-listener", "windows-key-listener.exe"):
            by_arch = manifest["binaries"][name]["sha256_by_arch"]
            assert "x86_64" in by_arch, (
                f"'{name}.sha256_by_arch' must have an 'x86_64' "
                f"sub-key (the legacy file name is emitted on x86_64 hosts)."
            )
            assert "aarch64" in by_arch, (
                f"'{name}.sha256_by_arch' must have an 'aarch64' "
                f"sub-key ( aarch64 builds are new but use the same "
                f"legacy file name)."
            )

    def test_sha256_by_arch_x86_64_matches_flat_sha256(self):
        """flat ``sha256`` value (the flat field is a backward-compat"""
        manifest = load_binary_manifest()
        assert manifest is not None
        for name in ("linux-key-listener", "windows-key-listener.exe"):
            entry = manifest["binaries"][name]
            flat = entry["sha256"]
            by_arch_x86_64 = entry["sha256_by_arch"]["x86_64"]
            assert flat == by_arch_x86_64, (
                f"'{name}.sha256' ({flat}) must equal "
                f"'{name}.sha256_by_arch.x86_64' ({by_arch_x86_64}), "
                f"the flat field is the backward-compat default for the "
                f"x86_64 arch."
            )

    def test_sha256_by_arch_aarch64_is_empty_in_dev_tree(self):
        """+ the ``sha256_by_arch.aarch64`` value is"""
        manifest = load_binary_manifest()
        assert manifest is not None
        for name in ("linux-key-listener", "windows-key-listener.exe"):
            by_arch_aarch64 = manifest["binaries"][name]["sha256_by_arch"]["aarch64"]
            assert by_arch_aarch64 == "", (
                f"'{name}.sha256_by_arch.aarch64' must be empty in "
                f"the dev tree (aarch64 builds pending the "
                f"update_native_manifests.py script from Fix-4). Got "
                f"{by_arch_aarch64!r}. If Fix-4 has landed, update this "
                f"test to assert the populated value."
            )

    def test_linux_x86_64_sha256_by_arch_matches_actual_binary(self):
        """the ``linux-key-listener.sha256_by_arch.x86_64``"""
        native_dir = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "native"
        real_binary = native_dir / "linux-key-listener"
        if not real_binary.is_file():
            pytest.skip("linux-key-listener binary not built in this tree")
        manifest = load_binary_manifest()
        assert manifest is not None
        expected = manifest["binaries"]["linux-key-listener"]["sha256_by_arch"]["x86_64"]
        assert expected, "linux-key-listener.sha256_by_arch.x86_64 must be populated"
        actual = hashlib.sha256(real_binary.read_bytes()).hexdigest()
        assert actual == expected, (
            f"linux-key-listener.sha256_by_arch.x86_64 is STALE: "
            f"manifest says {expected}, actual binary is {actual}. "
            f"Run scripts/build/update_native_manifests.py to refresh."
        )

    def test_windows_x86_64_sha256_by_arch_matches_actual_binary(self):
        """``windows-key-listener.exe.sha256_by_arch.x86_64`` value MUST"""
        native_dir = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "native"
        real_binary = native_dir / "windows-key-listener.exe"
        if not real_binary.is_file():
            pytest.skip("windows-key-listener.exe not committed in this tree")
        manifest = load_binary_manifest()
        assert manifest is not None
        expected = manifest["binaries"]["windows-key-listener.exe"]["sha256_by_arch"]["x86_64"]
        assert expected, "windows-key-listener.exe.sha256_by_arch.x86_64 must be populated"
        actual = hashlib.sha256(real_binary.read_bytes()).hexdigest()
        assert actual == expected, (
            f"windows-key-listener.exe.sha256_by_arch.x86_64 is STALE: "
            f"manifest says {expected}, actual binary is {actual}. "
            f"Run scripts/build/update_native_manifests.py to refresh."
        )
