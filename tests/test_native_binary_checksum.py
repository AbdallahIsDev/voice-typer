"""Regression tests for the native binary SHA-256 checksum verification
introduced by CR-46, updated for G4-L-11 (arch-suffixed manifest names)
and G4-L-09 + G4-H-34 (trusted-path override hardening).

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
  with the "trusted env var override" and "no manifest entry" fail-closed
  paths, called by the factory after :func:`get_native_binary_path`.

G4-L-11 (this file): the manifest is now keyed by the arch-suffixed
binary names that the build actually emits
(``linux-key-listener-x86_64``, ``linux-key-listener-aarch64``,
``windows-key-listener-x86_64.exe``, ``windows-key-listener-aarch64.exe``,
``macos-key-listener``). The pre-fix tests used the legacy non-suffixed
names (``linux-key-listener``, ``windows-key-listener.exe``) which no
longer match any manifest entry, so 6 tests failed. This file now:

  1. Uses arch-suffixed names everywhere.
  2. Updates ``test_skips_when_no_manifest_entry`` to assert CR-002
     fail-closed behavior (returns ``False``, not ``True``).
  3. Adds a parametrized test that runs ``verify_native_binary_or_skip``
     against each manifest entry name.

G4-L-09 + G4-H-34: the trusted-path override now requires BOTH the
``VOICE_TYPER_NATIVE_TRUST=1`` confirmation env var AND the discovered
path actually living under an env-specified directory. Tests in
``TestIsTrustedPathOverride`` and ``TestVerifyNativeBinaryOrSkip`` are
updated to reflect the new contract.

These tests pin:

1. ``verify_native_binary`` accepts a binary whose hash matches the
   expected value.
2. ``verify_native_binary`` REJECTS a tampered binary (different
   content → different hash).
3. ``verify_native_binary_or_skip`` skips verification when BOTH the
   ``VOICE_TYPER_NATIVE_TRUST=1`` env var is set AND the discovered
   path lives under (or equals) the env-specified location.
4. ``verify_native_binary_or_skip`` FAILS CLOSED (returns ``False``)
   when the manifest has no entry for the binary name — CR-002.
5. ``get_expected_sha256`` returns ``None`` for unknown binary names
   and for entries with an empty ``sha256`` field.
6. The factory's ``create_native_backend`` returns ``None`` (triggering
   legacy fallback) when the discovered binary's checksum doesn't
   match the manifest.
7. The factory's ``is_native_backend_available`` returns ``False``
   when the discovered binary's checksum doesn't match the manifest.
8. A parametrized test runs ``verify_native_binary_or_skip`` against
   each manifest entry name (G4-L-11).
"""

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

# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def clean_env(monkeypatch):
    """Remove trusted-override env vars so verification actually runs.

    G4-L-09 + G4-H-34: the override now requires BOTH
    ``VOICE_TYPER_NATIVE_TRUST=1`` AND a path/dir env var, so the
    fixture clears all three to ensure a known-clean baseline.
    """
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
    """Tests for ``get_expected_sha256(binary_name)`` — manifest lookup.

    G4-L-11: the manifest is now keyed by the arch-suffixed names that
    the build actually emits. The legacy non-suffixed names
    (``linux-key-listener``, ``windows-key-listener.exe``) are NOT in
    the manifest, so lookups for those names return ``None``.
    """

    def test_returns_none_for_unknown_binary(self):
        """An unknown binary name (not in the manifest) returns None."""
        assert get_expected_sha256("nonexistent-binary-name-xyz") is None

    def test_returns_none_for_legacy_non_suffixed_names(self):
        """G4-L-11: the legacy non-arch-suffixed names are no longer in
        the manifest — lookups for them return None (CR-002 fail-closed
        will trigger if a binary with the legacy name is discovered).
        """
        assert get_expected_sha256("linux-key-listener") is None
        assert get_expected_sha256("windows-key-listener.exe") is None

    def test_returns_none_for_empty_sha256_entry(self):
        """A manifest entry with an empty sha256 field returns None.

        The in-tree dev manifest leaves macOS/Windows sha256 empty
        because those binaries aren't built on the Linux dev host.
        ``get_expected_sha256`` must return None so the caller FAILS
        CLOSED (CR-002) rather than silently trusting the binary.
        """
        # The shipped manifest has empty sha256 for these arch-suffixed
        # entries (only linux-x86_64 has a real hash in the dev tree).
        assert get_expected_sha256("macos-key-listener") is None
        assert get_expected_sha256("windows-key-listener-x86_64.exe") is None
        assert get_expected_sha256("windows-key-listener-aarch64.exe") is None
        assert get_expected_sha256("linux-key-listener-aarch64") is None

    def test_returns_hash_for_known_binary(self):
        """A manifest entry with a non-empty sha256 returns the hash (lowercased).

        G4-L-11: the manifest is keyed by ``linux-key-listener-x86_64``
        (the arch-suffixed name produced by compile_native.sh on a
        Linux x86_64 host).
        """
        sha = get_expected_sha256("linux-key-listener-x86_64")
        assert sha is not None
        assert len(sha) == 64  # SHA-256 hex digest length
        assert sha == sha.lower()  # normalized to lowercase

    def test_returns_none_when_manifest_missing(self, monkeypatch, tmp_path):
        """If the manifest file doesn't exist, return None (skip verification)."""
        # Point _MANIFEST_PATH at a non-existent file by monkeypatching
        # the module-level constant.
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


# ─── _is_trusted_path_override (G4-L-09 + G4-H-34) ────────────────────────


class TestIsTrustedPathOverride:
    """Tests for the env-var trusted-override check.

    G4-L-09 + G4-H-34: the override now requires BOTH:
      1. ``VOICE_TYPER_NATIVE_TRUST=1`` (paired confirmation env var).
      2. ``VOICE_TYPER_NATIVE_BINARY`` OR ``VOICE_TYPER_NATIVE_DIR`` set.
    """

    def test_returns_false_when_no_env_vars(self, monkeypatch):
        """No env vars set → not a trusted override → verification runs."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        assert _is_trusted_path_override() is False

    def test_returns_false_when_native_binary_set_without_trust(self, monkeypatch):
        """G4-L-09: ``VOICE_TYPER_NATIVE_BINARY`` alone is NOT enough —
        the paired ``VOICE_TYPER_NATIVE_TRUST=1`` confirmation is also
        required, so an attacker with env-var write access cannot
        silently disable the checksum gate.
        """
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
        """G4-L-09: ``VOICE_TYPER_NATIVE_TRUST=1`` alone is NOT enough —
        the user must also specify which path/dir to trust.
        """
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "1")
        assert _is_trusted_path_override() is False

    def test_returns_true_when_trust_and_native_binary_set(self, monkeypatch):
        """G4-L-09: ``VOICE_TYPER_NATIVE_TRUST=1`` +
        ``VOICE_TYPER_NATIVE_BINARY`` → trusted override.
        """
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", "/custom/path/binary")
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "1")
        assert _is_trusted_path_override() is True

    def test_returns_true_when_trust_and_native_dir_set(self, monkeypatch):
        """G4-L-09: ``VOICE_TYPER_NATIVE_TRUST=1`` +
        ``VOICE_TYPER_NATIVE_DIR`` → trusted override.
        """
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
        """G4-L-09: ``VOICE_TYPER_NATIVE_TRUST`` must be exactly ``"1"``."""
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", "/a/b")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "true")
        assert _is_trusted_path_override() is False

    def test_returns_false_when_trust_set_to_empty(self, monkeypatch):
        """G4-L-09: an empty ``VOICE_TYPER_NATIVE_TRUST`` is treated as unset."""
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", "/a/b")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "")
        assert _is_trusted_path_override() is False


# ─── _path_matches_env_override (G4-H-34) ─────────────────────────────────


class TestPathMatchesEnvOverride:
    """G4-H-34: ``verify_native_binary_or_skip`` only skips verification
    when the discovered binary path actually lives under (or equals) one
    of the env-specified paths.
    """

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
        """G4-H-34: setting ``VOICE_TYPER_NATIVE_DIR`` does NOT bypass
        verification for binaries discovered elsewhere via fallback search.
        """
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
        """G4-H-34: path-prefix-based check must NOT be fooled by sibling
        directories with a common string prefix (e.g.
        ``/opt/vt/native`` vs ``/opt/vt/native2``).
        """
        env_dir = tmp_path / "native"
        env_dir.mkdir()
        sibling_dir = tmp_path / "native2"  # common string prefix
        sibling_dir.mkdir()
        binary = sibling_dir / "linux-key-listener-x86_64"
        binary.write_text("x")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(env_dir))
        assert _path_matches_env_override(binary) is False


# ─── verify_native_binary_or_skip (composed helper) ───────────────────────


class TestVerifyNativeBinaryOrSkip:
    """Tests for the composed helper used by the factory."""

    def test_skips_when_trusted_override_set_and_path_matches(self, monkeypatch, fake_binary):
        """G4-L-09 + G4-H-34: when ``VOICE_TYPER_NATIVE_TRUST=1`` is set
        AND the discovered path matches the env-specified location,
        skip verification (return True).
        """
        # Set both the trust flag AND a path env var pointing at the
        # fake_binary's parent dir.
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(fake_binary.parent))
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "1")
        # Even with a 'tampered' binary (hash won't match the manifest),
        # the trusted override + path match means we skip verification.
        assert verify_native_binary_or_skip(fake_binary) is True

    def test_does_not_skip_when_trust_set_but_path_does_not_match(self, monkeypatch, fake_binary, tmp_path):
        """G4-H-34: setting ``VOICE_TYPER_NATIVE_TRUST=1`` +
        ``VOICE_TYPER_NATIVE_DIR`` does NOT bypass verification for a
        binary discovered outside the env dir. The function falls
        through to the manifest check, which fails closed for an
        unknown binary name.
        """
        env_dir = tmp_path / "env-specified"
        env_dir.mkdir()
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(env_dir))
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "1")
        # fake_binary lives OUTSIDE env_dir → path does NOT match →
        # the trust bypass does NOT apply → falls through to manifest
        # lookup → fake_binary.name is "fake-key-listener" (not in
        # manifest) → CR-002 fail-closed returns False.
        assert verify_native_binary_or_skip(fake_binary) is False

    def test_does_not_skip_when_path_matches_but_trust_unset(self, monkeypatch, fake_binary):
        """G4-L-09: setting the path env var alone (without TRUST=1) is
        not enough — falls through to manifest lookup, which fails
        closed for an unknown binary name.
        """
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(fake_binary.parent))
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        assert verify_native_binary_or_skip(fake_binary) is False

    def test_fails_closed_when_no_manifest_entry(self, monkeypatch, fake_binary):
        """G4-L-11 + CR-002: when the manifest has no entry for the binary
        name, FAIL CLOSED (return False) — do NOT silently trust the binary.

        ``fake_binary.name`` is ``"fake-key-listener"`` which is not in
        the manifest. Pre-CR-002 this returned True (silently trusted);
        post-CR-002 it must return False so the factory falls back to
        the legacy backend rather than spawning an unverified binary.
        """
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        assert verify_native_binary_or_skip(fake_binary) is False

    def test_fails_closed_logs_error(self, monkeypatch, fake_binary, caplog):
        """G4-L-11 + CR-002: the fail-closed branch logs an ERROR so
        operators can see WHY the binary was rejected.
        """
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
        """When the manifest entry exists and the checksum doesn't match, reject.

        This is the core CR-46 supply-chain gate: a tampered
        ``linux-key-listener-x86_64`` binary (whose content doesn't
        match the manifest's sha256) is REJECTED, causing the factory
        to fall back to the legacy backend.
        """
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        # Rename the tampered binary to the arch-suffixed name (G4-L-11)
        # so the manifest lookup finds a non-None expected sha256.
        named = tampered_binary.parent / "linux-key-listener-x86_64"
        tampered_binary.rename(named)
        # The manifest's linux-key-listener-x86_64 sha256 is the hash
        # of the REAL linux binary — the tampered content won't match.
        assert verify_native_binary_or_skip(named) is False

    def test_accepts_when_checksum_matches(self, monkeypatch, tmp_path):
        """When the manifest entry exists and the checksum matches, accept."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)
        # Create a binary whose content matches the manifest's sha256
        # for linux-key-listener-x86_64 (G4-L-11: arch-suffixed name).
        # We do this by reading the REAL linux-key-listener-x86_64
        # binary from the source tree (which is the basis for the
        # manifest's sha256) and copying it to a temp path with the
        # arch-suffixed name.
        real_binary = (
            Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "native" / "linux-key-listener-x86_64"
        )
        if not real_binary.is_file():
            pytest.skip("linux-key-listener-x86_64 binary not built in this tree")
        named = tmp_path / "linux-key-listener-x86_64"
        named.write_bytes(real_binary.read_bytes())
        assert verify_native_binary_or_skip(named) is True


# ─── Parametrized manifest entry coverage (G4-L-11) ──────────────────────


class TestManifestEntryParametrized:
    """G4-L-11: a parametrized test that runs ``verify_native_binary_or_skip``
    against each manifest entry name to catch future regressions where a
    manifest entry exists but ``verify_native_binary_or_skip`` mishandles
    the binary (e.g. returns True for an empty-sha256 entry — the CR-002
    bug this test class guards against).

    The test creates a temp file named after the manifest entry (so the
    name lookup hits a real entry), then asserts the function returns
    ``False`` for entries with empty sha256 (CR-002 fail-closed) or
    ``False`` for entries with non-empty sha256 whose content doesn't
    match (checksum mismatch). The only way for the function to return
    ``True`` is via the trusted-path override, which is not set here.
    """

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
        ],
    )
    def test_each_manifest_entry_is_handled(self, entry_name, tmp_path):
        """G4-L-11: every manifest entry is handled correctly by
        ``verify_native_binary_or_skip``. A temp file named after the
        manifest entry is created with bogus content; the function
        must return False (either fail-closed for empty-sha256 entries
        OR checksum-mismatch for entries with a real sha256).
        """
        # Create a temp binary with the manifest entry name.
        named = tmp_path / entry_name
        named.write_bytes(b"#!bogus content for parametrized test\n")
        # The function must NOT return True — there's no trusted override
        # set, and either the manifest entry is missing (returns None →
        # fail-closed False), the sha256 is empty (returns None → fail-
        # closed False), or the sha256 is non-empty but the content
        # doesn't match (checksum mismatch → False).
        result = verify_native_binary_or_skip(named)
        assert result is False, (
            f"verify_native_binary_or_skip({entry_name}) returned {result}; "
            f"expected False (fail-closed or checksum-mismatch) for a bogus binary "
            f"without a trusted-override env var."
        )


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

        # G4-L-11: rename tampered binary to the arch-suffixed name
        # (linux-key-listener-x86_64) so the manifest lookup finds a
        # non-None expected sha256.
        named = tampered_binary.parent / "linux-key-listener-x86_64"
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

        # G4-L-11: use the arch-suffixed name that the manifest is keyed by.
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
        """G4-L-09 + G4-H-34: with BOTH ``VOICE_TYPER_NATIVE_TRUST=1`` AND
        a path env var pointing at the tampered binary's location, the
        factory accepts the binary without checksum verification.

        This is intentional: the trust flag + path env var together
        constitute an explicit user/admin override that already implies
        root-level trust. The checksum gate is meant to catch tampering
        with the in-tree and bundled binaries the user does NOT
        explicitly choose.
        """
        from voice_typer.server.native_hotkeys import factory as factory_mod

        # G4-L-09: set BOTH the trust flag AND the path env var.
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(tampered_binary.parent))
        monkeypatch.setenv("VOICE_TYPER_NATIVE_TRUST", "1")
        # Name the tampered binary correctly (arch-suffixed) for the
        # platform — but the name doesn't matter for the trusted-override
        # path because verification is skipped entirely.
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
        """G4-L-09: setting ``VOICE_TYPER_NATIVE_DIR`` WITHOUT
        ``VOICE_TYPER_NATIVE_TRUST=1`` does NOT skip verification — the
        factory falls through to the manifest check, which fails closed
        for a tampered binary.
        """
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
            "verification — the tampered binary must be rejected."
        )


# ─── Manifest sanity ──────────────────────────────────────────────────────


class TestManifestSanity:
    """Sanity checks on the shipped ``binaries.json`` manifest.

    G4-L-11: the manifest is keyed by the arch-suffixed names that the
    build actually emits. The legacy non-suffixed names
    (``linux-key-listener``, ``windows-key-listener.exe``) are GONE.
    """

    def test_manifest_file_exists(self):
        """The manifest file exists at the expected path."""
        assert _MANIFEST_PATH.is_file(), (
            f"CR-46 manifest not found at {_MANIFEST_PATH}. "
            "Run scripts/build/compile_native.sh + update_native_manifests.py."
        )

    def test_manifest_is_valid_json(self):
        """The manifest is valid JSON with the expected shape.

        G4-L-11: the manifest must contain entries for all five
        arch-suffixed binary names that the build emits.
        """
        manifest = load_binary_manifest()
        assert manifest is not None
        assert "version" in manifest
        assert "binaries" in manifest
        binaries = manifest["binaries"]
        assert isinstance(binaries, dict)
        # G4-L-11: all five arch-suffixed binary names must be present
        # (sha256 may be empty for platforms not built in the current
        # dev tree, but the entries must exist so the manifest lookup
        # at least finds an entry and fails-closed with a clear "empty
        # sha256" error rather than a "missing entry" error).
        for name in (
            "linux-key-listener-x86_64",
            "linux-key-listener-aarch64",
            "windows-key-listener-x86_64.exe",
            "windows-key-listener-aarch64.exe",
            "macos-key-listener",
        ):
            assert name in binaries, f"Manifest missing entry for {name}"
            entry = binaries[name]
            assert isinstance(entry, dict)
            assert "sha256" in entry
            assert "version" in entry
            assert "min_proto_version" in entry

    def test_manifest_does_not_have_legacy_non_suffixed_entries(self):
        """G4-L-11: the legacy non-suffixed names (which did not match
        the build output) MUST NOT be present — their presence would
        imply a stale manifest that needs re-running of
        ``update_native_manifests.py``.
        """
        manifest = load_binary_manifest()
        assert manifest is not None
        binaries = manifest["binaries"]
        # The legacy names that CR-002 found were silently failing.
        assert "linux-key-listener" not in binaries, (
            "Manifest still has legacy 'linux-key-listener' entry — "
            "this name does not match the build output (linux-key-listener-x86_64). "
            "Run scripts/build/update_native_manifests.py."
        )
        assert "windows-key-listener.exe" not in binaries, (
            "Manifest still has legacy 'windows-key-listener.exe' entry — "
            "this name does not match the build output (windows-key-listener-x86_64.exe). "
            "Run scripts/build/update_native_manifests.py."
        )

    def test_linux_binary_sha256_matches_actual_binary(self):
        """The manifest's linux-key-listener-x86_64 sha256 matches the
        actual binary (G4-L-11: arch-suffixed name).

        This catches a stale manifest: if the binary was rebuilt but the
        manifest wasn't updated, this test fails.
        """
        # G4-L-11: use the arch-suffixed name.
        real_binary = (
            Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "native" / "linux-key-listener-x86_64"
        )
        if not real_binary.is_file():
            pytest.skip("linux-key-listener-x86_64 binary not built in this tree")
        expected = get_expected_sha256("linux-key-listener-x86_64")
        assert expected is not None, "Manifest must have a non-empty sha256 for linux-key-listener-x86_64"
        actual = hashlib.sha256(real_binary.read_bytes()).hexdigest()
        assert actual == expected, (
            f"Manifest sha256 for linux-key-listener-x86_64 is STALE: "
            f"manifest says {expected}, actual binary is {actual}. "
            f"Run scripts/build/update_native_manifests.py to refresh."
        )
