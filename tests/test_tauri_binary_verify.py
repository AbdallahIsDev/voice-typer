"""Behavioral tests for ``verify_tauri_binary_or_skip`` (EO-32).

The Tauri autostart launcher spawns the ``voice-typer-tauri`` host
binary at login. ``verify_tauri_binary_or_skip`` is the CR-002
fail-closed integrity gate: the binary MUST verify against
``tauri-binaries.json`` before it is spawned, otherwise a tampered or
stale binary (or the ``VT_TAURI_BINARY`` env override, which is NOT a
bypass) would launch unchecked.

Contract (from ``tauri-binaries.json`` ``_manifest_loader_contract``):

- Manifest missing / unreadable → FAIL CLOSED (False).
- No manifest entry for ``Path(path).name`` → FAIL CLOSED.
- Per-(platform, arch) sha256 sub-key missing or empty → FAIL CLOSED
  (production builds populate every sub-key via
  ``scripts/build/update_tauri_manifests.py``).
- SHA-256 mismatch → FAIL CLOSED.
- SHA-256 match → True.

The platform/arch key mirrors ``_tauri_manifest_key``: ``darwin`` →
``macos`` (single universal key); ``amd64`` → ``x86_64``.
"""

from __future__ import annotations

import hashlib
import json

from voice_typer.server.autostart_launcher import (
    _tauri_manifest_key,
    verify_tauri_binary_or_skip,
)


def _write_manifest(tmp_path, binary_name: str, key: str, sha: str) -> object:
    """Write a minimal valid manifest and return its path (as a string)."""
    manifest = {
        "version": 1,
        "binaries": {
            binary_name: {
                "sha256": {key: sha},
                "_platforms": [],
                "_install_paths": [],
            }
        },
    }
    path = tmp_path / "tauri-binaries.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return str(path)


class TestVerifyTauriBinaryOrSkip:
    """CR-002 fail-closed behavior for the Tauri binary integrity gate."""

    def test_matching_sha256_returns_true(self, tmp_path, monkeypatch):
        """A binary whose SHA-256 matches the manifest entry passes."""
        binary = tmp_path / "voice-typer-tauri"
        binary.write_bytes(b"fake tauri binary bytes")
        sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        monkeypatch.setenv(
            "VT_TAURI_MANIFEST", _write_manifest(tmp_path, "voice-typer-tauri", _tauri_manifest_key(), sha)
        )
        assert verify_tauri_binary_or_skip(binary) is True

    def test_manifest_missing_fails_closed(self, tmp_path, monkeypatch):
        """No manifest anywhere → refuse to spawn (False)."""
        binary = tmp_path / "voice-typer-tauri"
        binary.write_bytes(b"bytes")
        monkeypatch.delenv("VT_TAURI_MANIFEST", raising=False)
        # Point the repo-root lookup at a dir with no manifest by
        # overriding the module constant path resolution via env.
        # Without VT_TAURI_MANIFEST the real repo-root manifest may
        # exist — force a guaranteed-missing location instead.
        monkeypatch.setenv("VT_TAURI_MANIFEST", str(tmp_path / "nope.json"))
        assert verify_tauri_binary_or_skip(binary) is False

    def test_unreadable_manifest_fails_closed(self, tmp_path, monkeypatch):
        """A malformed manifest file → refuse to spawn (False)."""
        binary = tmp_path / "voice-typer-tauri"
        binary.write_bytes(b"bytes")
        manifest = tmp_path / "tauri-binaries.json"
        manifest.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setenv("VT_TAURI_MANIFEST", str(manifest))
        assert verify_tauri_binary_or_skip(binary) is False

    def test_missing_entry_fails_closed(self, tmp_path, monkeypatch):
        """Binary name not in the manifest → refuse to spawn (False)."""
        binary = tmp_path / "voice-typer-tauri"
        binary.write_bytes(b"bytes")
        monkeypatch.setenv(
            "VT_TAURI_MANIFEST",
            _write_manifest(tmp_path, "some-other-binary", _tauri_manifest_key(), "a" * 64),
        )
        assert verify_tauri_binary_or_skip(binary) is False

    def test_empty_sha256_fails_closed(self, tmp_path, monkeypatch):
        """Empty per-arch sha256 (dev tree, not release-built) → FAIL CLOSED."""
        binary = tmp_path / "voice-typer-tauri"
        binary.write_bytes(b"bytes")
        monkeypatch.setenv(
            "VT_TAURI_MANIFEST",
            _write_manifest(tmp_path, "voice-typer-tauri", _tauri_manifest_key(), ""),
        )
        assert verify_tauri_binary_or_skip(binary) is False

    def test_missing_arch_key_fails_closed(self, tmp_path, monkeypatch):
        """Manifest entry lacks the running platform/arch sub-key → False."""
        binary = tmp_path / "voice-typer-tauri"
        binary.write_bytes(b"bytes")
        manifest = {
            "version": 1,
            "binaries": {
                "voice-typer-tauri": {
                    "sha256": {"linux-aarch64": "a" * 64},
                    "_platforms": [],
                    "_install_paths": [],
                }
            },
        }
        path = tmp_path / "tauri-binaries.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        monkeypatch.setenv("VT_TAURI_MANIFEST", str(path))
        assert verify_tauri_binary_or_skip(binary) is False

    def test_sha256_mismatch_fails_closed(self, tmp_path, monkeypatch):
        """Tampered binary (hash differs from manifest) → refuse to spawn."""
        binary = tmp_path / "voice-typer-tauri"
        binary.write_bytes(b"original bytes")
        monkeypatch.setenv(
            "VT_TAURI_MANIFEST",
            _write_manifest(tmp_path, "voice-typer-tauri", _tauri_manifest_key(), "b" * 64),
        )
        # Tamper AFTER the manifest was written (the manifest pins the
        # hash of the *original* bytes).
        binary.write_bytes(b"tampered bytes")
        assert verify_tauri_binary_or_skip(binary) is False

    def test_binary_read_failure_fails_closed(self, tmp_path, monkeypatch):
        """Binary unreadable at hash time → refuse to spawn (False)."""
        binary = tmp_path / "voice-typer-tauri"
        # Do NOT create the file — read_bytes() raises FileNotFoundError.
        monkeypatch.setenv(
            "VT_TAURI_MANIFEST",
            _write_manifest(tmp_path, "voice-typer-tauri", _tauri_manifest_key(), "c" * 64),
        )
        assert verify_tauri_binary_or_skip(binary) is False

    def test_accepts_str_path(self, tmp_path, monkeypatch):
        """The helper accepts a ``str`` path as well as ``Path``."""
        binary = tmp_path / "voice-typer-tauri"
        binary.write_bytes(b"fake tauri binary bytes")
        sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        monkeypatch.setenv(
            "VT_TAURI_MANIFEST", _write_manifest(tmp_path, "voice-typer-tauri", _tauri_manifest_key(), sha)
        )
        assert verify_tauri_binary_or_skip(str(binary)) is True


class TestTauriManifestKey:
    """The platform/arch key resolution mirrors the manifest contract."""

    def test_returns_string(self):
        assert isinstance(_tauri_manifest_key(), str)
        assert _tauri_manifest_key()
