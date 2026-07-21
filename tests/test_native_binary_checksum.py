"""Regression tests for the native binary SHA-256 checksum verification
introduced by CR-46.

CR-46 found that ``get_native_binary_path`` discovered the native
key-listener binary via ``Path.is_file()`` checks only — no SHA-256,
no code-signature check, no version stamp. A malicious actor with write
access to ``voice_typer/server/native/`` (or to the PyInstaller
bundle's resource dir) could replace the binary with a keylogger that
emits the same ``READY`` / ``KEY_DOWN:*`` / ``MOD_DOWN:*`` wire
protocol while exfiltrating keystrokes.

The fix adds:

- ``voice_typer/server/native/binaries.json`` — manifest mapping
  ``binary_name → {sha256, version, min_proto_version}``.
- :func:`voice_typer.server.native_hotkeys.binary_path.verify_native_binary`
  — computes ``hashlib.sha256(path.read_bytes()).hexdigest()`` and
  compares against the expected value.
- :func:`verify_native_binary_or_skip` — composes the manifest lookup
  with the "trusted env var override" and "no manifest entry" skip
  paths, called by the factory after :func:`get_native_binary_path`.

These tests pin:

1. ``verify_native_binary`` accepts a binary whose hash matches the
   expected value.
2. ``verify_native_binary`` REJECTS a tampered binary (different
   content → different hash).
3. ``verify_native_binary_or_skip`` skips verification when a trusted
   env var override (``VOICE_TYPER_NATIVE_BINARY`` /
   ``VOICE_TYPER_NATIVE_DIR``) is set — the env var is an explicit
   user/admin override that already implies root-level trust.
4. ``verify_native_binary_or_skip`` skips verification when the
   manifest has no entry for the binary (e.g. a brand-new binary
   whose sha256 hasn't been populated yet, or an empty sha256 field
   for a binary not built in the current dev tree).
5. ``get_expected_sha256`` returns ``None`` for unknown binary names
   and for entries with an empty ``sha256`` field.
6. The factory's ``create_native_backend`` returns ``None`` (triggering
   legacy fallback) when the discovered binary's checksum doesn't
   match the manifest.
7. The factory's ``is_native_backend_available`` returns ``False``
   when the discovered binary's checksum doesn't match the manifest.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from voice_typer.server.native_hotkeys.binary_path import (
    _MANIFEST_PATH,
    _is_trusted_path_override,
    get_expected_sha256,
    load_binary_manifest,
    verify_native_binary,
    verify_native_binary_or_skip,
)

# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def clean_env(monkeypatch):
    """Remove trusted-override env vars so verification actually runs."""
    monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
    monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)


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


# ─── verify_native_binary (pure function) ─────────────────────────────────


class TestVerifyNativeBinary:
    """Tests for the pure ``verify_native_binary(path, expected_sha256)`` function."""

    def test_accepts_matching_hash(self, fake_binary, caplog):
        """A binary whose SHA-256 matches the expected value is accepted."""
        expected = _sha256(fake_binary)
        with caplog.at_level("DEBUG"):
            assert verify_native_binary(fake_binary, expected) is True

    def test_rejects_tampered_binary(self, fake_binary, tampered_binary, caplog):
        """A tampered binary (different content → different hash) is rejected.

        This is the core CR-46 regression: the manifest carries the hash
        of the legitimate ``fake_binary``, but we verify ``tampered_binary``
        (which has different content). The function must return False
        and log an ERROR naming both hashes so the operator can
        investigate.
        """
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
        # Use a path that doesn't exist — read_bytes() raises FileNotFoundError
        # (a subclass of OSError).
        missing = tmp_path / "does-not-exist"
        assert verify_native_binary(missing, "0" * 64) is False

    def test_accepts_matching_hash_with_whitespace(self, fake_binary):
        """Leading/trailing whitespace in the expected hash is stripped."""
        expected = _sha256(fake_binary)
        assert verify_native_binary(fake_binary, f"  {expected}  ") is True


# ─── get_expected_sha256 (manifest lookup) ────────────────────────────────


class TestGetExpectedSha256:
    """Tests for ``get_expected_sha256(binary_name)`` — manifest lookup."""

    def test_returns_none_for_unknown_binary(self):
        """An unknown binary name (not in the manifest) returns None."""
        assert get_expected_sha256("nonexistent-binary-name-xyz") is None

    def test_returns_none_for_empty_sha256_entry(self):
        """A manifest entry with an empty sha256 field returns None.

        The in-tree dev manifest leaves macOS/Windows sha256 empty
        because those binaries aren't built on the Linux dev host.
        ``get_expected_sha256`` must return None so the caller skips
        verification (we can't verify what we don't have an expected
        hash for).
        """
        # The shipped manifest has empty sha256 for macos-key-listener
        # and windows-key-listener.exe (only linux-key-listener has a
        # real hash, because only the Linux binary is built in-tree).
        assert get_expected_sha256("macos-key-listener") is None
        assert get_expected_sha256("windows-key-listener.exe") is None

    def test_returns_hash_for_known_binary(self):
        """A manifest entry with a non-empty sha256 returns the hash (lowercased)."""
        # The shipped manifest has a real sha256 for linux-key-listener.
        sha = get_expected_sha256("linux-key-listener")
        assert sha is not None
        assert len(sha) == 64  # SHA-256 hex digest length
        assert sha == sha.lower()  # normalized to lowercase

    def test_returns_none_when_manifest_missing(self, monkeypatch, tmp_path):
        """If the manifest file doesn't exist, return None (skip verification)."""
        # Point _MANIFEST_PATH at a non-existent file by monkeypatching
        # the module-level constant.
        from voice_typer.server.native_hotkeys import binary_path as bp

        monkeypatch.setattr(bp, "_MANIFEST_PATH", tmp_path / "no-such-manifest.json")
        assert get_expected_sha256("linux-key-listener") is None

    def test_returns_none_when_manifest_malformed(self, monkeypatch, tmp_path):
        """If the manifest is malformed JSON, return None (skip verification)."""
        from voice_typer.server.native_hotkeys import binary_path as bp

        bad = tmp_path / "bad-manifest.json"
        bad.write_text("{ this is not valid JSON")
        monkeypatch.setattr(bp, "_MANIFEST_PATH", bad)
        assert get_expected_sha256("linux-key-listener") is None


# ─── _is_trusted_path_override ────────────────────────────────────────────


class TestIsTrustedPathOverride:
    """Tests for the env-var trusted-override check."""

    def test_returns_false_when_no_env_vars(self, monkeypatch):
        """No env vars set → not a trusted override → verification runs."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        assert _is_trusted_path_override() is False

    def test_returns_true_when_native_binary_set(self, monkeypatch):
        """VOICE_TYPER_NATIVE_BINARY set → trusted override → skip verification."""
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", "/custom/path/binary")
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        assert _is_trusted_path_override() is True

    def test_returns_true_when_native_dir_set(self, monkeypatch):
        """VOICE_TYPER_NATIVE_DIR set → trusted override → skip verification."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", "/custom/dir")
        assert _is_trusted_path_override() is True

    def test_returns_true_when_both_set(self, monkeypatch):
        """Both env vars set → trusted override."""
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", "/a/b")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", "/c/d")
        assert _is_trusted_path_override() is True


# ─── verify_native_binary_or_skip (composed helper) ───────────────────────


class TestVerifyNativeBinaryOrSkip:
    """Tests for the composed helper used by the factory."""

    def test_skips_when_trusted_override_set(self, monkeypatch, fake_binary):
        """When a trusted env var is set, skip verification (return True)."""
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", "/custom/dir")
        # Even with a 'tampered' binary (hash won't match the manifest),
        # the trusted override means we skip verification entirely.
        assert verify_native_binary_or_skip(fake_binary) is True

    def test_skips_when_no_manifest_entry(self, monkeypatch, fake_binary):
        """When the manifest has no entry for the binary name, skip (return True)."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        # fake_binary's name is "fake-key-listener" — not in the manifest.
        assert verify_native_binary_or_skip(fake_binary) is True

    def test_rejects_when_checksum_mismatches(self, monkeypatch, tampered_binary):
        """When the manifest entry exists and the checksum doesn't match, reject.

        This is the core CR-46 supply-chain gate: a tampered
        ``linux-key-listener`` binary (whose content doesn't match the
        manifest's sha256) is REJECTED, causing the factory to fall
        back to the legacy backend.
        """
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        # Rename the tampered binary to "linux-key-listener" so the
        # manifest lookup finds a non-None expected sha256.
        named = tampered_binary.parent / "linux-key-listener"
        tampered_binary.rename(named)
        # The manifest's linux-key-listener sha256 is the hash of the
        # REAL linux binary — the tampered content won't match.
        assert verify_native_binary_or_skip(named) is False

    def test_accepts_when_checksum_matches(self, monkeypatch, tmp_path):
        """When the manifest entry exists and the checksum matches, accept."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        # Create a binary whose content matches the manifest's sha256
        # for linux-key-listener. We do this by reading the REAL
        # linux-key-listener binary from the source tree (which is the
        # basis for the manifest's sha256) and copying it to a temp
        # path named "linux-key-listener".
        real_binary = (
            Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "native" / "linux-key-listener"
        )
        if not real_binary.is_file():
            pytest.skip("linux-key-listener binary not built in this tree")
        named = tmp_path / "linux-key-listener"
        named.write_bytes(real_binary.read_bytes())
        assert verify_native_binary_or_skip(named) is True


# ─── Factory integration ──────────────────────────────────────────────────


class TestFactoryChecksumGate:
    """Integration tests: the factory's ``create_native_backend`` and
    ``is_native_backend_available`` enforce the checksum gate.

    These tests monkeypatch ``get_native_binary_path`` to return a
    tampered binary and verify the factory returns ``None`` (triggering
    legacy fallback) instead of spawning the tampered binary.
    """

    def test_create_native_backend_returns_none_for_tampered_binary(self, monkeypatch, tampered_binary, clean_env):
        """Factory returns None (→ legacy fallback) when the binary is tampered."""
        from voice_typer.server.native_hotkeys import factory as factory_mod

        # Rename tampered binary to linux-key-listener so the manifest
        # lookup finds a non-None expected sha256.
        named = tampered_binary.parent / "linux-key-listener"
        tampered_binary.rename(named)

        # Force the factory to think it's on Linux and the binary is
        # at our tampered path.
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

        named = tampered_binary.parent / "linux-key-listener"
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
            Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "native" / "linux-key-listener"
        )
        if not real_binary.is_file():
            pytest.skip("linux-key-listener binary not built in this tree")

        named = tmp_path / "linux-key-listener"
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
        """With a trusted env var override, even a tampered binary is accepted.

        This is intentional: the env var is an explicit user/admin
        override that already implies root-level trust. The checksum
        gate is meant to catch tampering with the in-tree and bundled
        binaries the user does NOT explicitly choose.
        """
        from voice_typer.server.native_hotkeys import factory as factory_mod

        # Set the trusted override env var
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(tampered_binary.parent))
        # Name the tampered binary correctly for the platform
        named = tampered_binary.parent / "linux-key-listener"
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
            "With a trusted env var override, the factory must accept the "
            "binary without checksum verification (even if 'tampered')"
        )


# ─── Manifest sanity ──────────────────────────────────────────────────────


class TestManifestSanity:
    """Sanity checks on the shipped ``binaries.json`` manifest."""

    def test_manifest_file_exists(self):
        """The manifest file exists at the expected path."""
        assert _MANIFEST_PATH.is_file(), (
            f"CR-46 manifest not found at {_MANIFEST_PATH}. "
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
        # All three platform binaries must be present (sha256 may be empty
        # for platforms not built in the current tree).
        for name in (
            "linux-key-listener",
            "macos-key-listener",
            "windows-key-listener.exe",
        ):
            assert name in binaries, f"Manifest missing entry for {name}"
            entry = binaries[name]
            assert isinstance(entry, dict)
            assert "sha256" in entry
            assert "version" in entry
            assert "min_proto_version" in entry

    def test_linux_binary_sha256_matches_actual_binary(self):
        """The manifest's linux-key-listener sha256 matches the actual binary.

        This catches a stale manifest: if the binary was rebuilt but the
        manifest wasn't updated, this test fails.
        """
        real_binary = (
            Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "native" / "linux-key-listener"
        )
        if not real_binary.is_file():
            pytest.skip("linux-key-listener binary not built in this tree")
        expected = get_expected_sha256("linux-key-listener")
        assert expected is not None, "Manifest must have a non-empty sha256 for linux-key-listener"
        actual = hashlib.sha256(real_binary.read_bytes()).hexdigest()
        assert actual == expected, (
            f"Manifest sha256 for linux-key-listener is STALE: "
            f"manifest says {expected}, actual binary is {actual}. "
            f"Run scripts/build/update_native_manifests.py to refresh."
        )
