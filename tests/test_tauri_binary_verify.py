"""Behavioral tests for ``verify_tauri_binary_or_skip`` (EO-32)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from voice_typer.server.autostart import tauri_spawn as _tauri_spawn_mod
from voice_typer.server.autostart_launcher import (
    _tauri_manifest_key,
    _tauri_manifest_path,
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
    """fail-closed behavior for the Tauri binary integrity gate."""

    def test_matching_sha256_returns_true(self, tmp_path, monkeypatch):
        """A binary whose SHA-256 matches the manifest entry passes."""
        binary = tmp_path / "lausu-tauri"
        binary.write_bytes(b"fake tauri binary bytes")
        sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        monkeypatch.setenv("VT_TAURI_MANIFEST", _write_manifest(tmp_path, "lausu-tauri", _tauri_manifest_key(), sha))
        assert verify_tauri_binary_or_skip(binary) is True

    def test_manifest_missing_fails_closed(self, tmp_path, monkeypatch):
        """No manifest anywhere → refuse to spawn (False)."""
        binary = tmp_path / "lausu-tauri"
        binary.write_bytes(b"bytes")
        monkeypatch.delenv("VT_TAURI_MANIFEST", raising=False)
        # Point the repo-root lookup at a dir with no manifest by
        monkeypatch.setenv("VT_TAURI_MANIFEST", str(tmp_path / "nope.json"))
        assert verify_tauri_binary_or_skip(binary) is False

    def test_unreadable_manifest_fails_closed(self, tmp_path, monkeypatch):
        """A malformed manifest file → refuse to spawn (False)."""
        binary = tmp_path / "lausu-tauri"
        binary.write_bytes(b"bytes")
        manifest = tmp_path / "tauri-binaries.json"
        manifest.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setenv("VT_TAURI_MANIFEST", str(manifest))
        assert verify_tauri_binary_or_skip(binary) is False

    def test_missing_entry_fails_closed(self, tmp_path, monkeypatch):
        """Binary name not in the manifest → refuse to spawn (False)."""
        binary = tmp_path / "lausu-tauri"
        binary.write_bytes(b"bytes")
        monkeypatch.setenv(
            "VT_TAURI_MANIFEST",
            _write_manifest(tmp_path, "some-other-binary", _tauri_manifest_key(), "a" * 64),
        )
        assert verify_tauri_binary_or_skip(binary) is False

    def test_empty_sha256_fails_closed(self, tmp_path, monkeypatch):
        """Empty per-arch sha256 (dev tree, not release-built) → FAIL CLOSED."""
        binary = tmp_path / "lausu-tauri"
        binary.write_bytes(b"bytes")
        monkeypatch.setenv(
            "VT_TAURI_MANIFEST",
            _write_manifest(tmp_path, "lausu-tauri", _tauri_manifest_key(), ""),
        )
        assert verify_tauri_binary_or_skip(binary) is False

    def test_missing_arch_key_fails_closed(self, tmp_path, monkeypatch):
        """Manifest entry lacks the running platform/arch sub-key → False."""
        binary = tmp_path / "lausu-tauri"
        binary.write_bytes(b"bytes")
        manifest = {
            "version": 1,
            "binaries": {
                "lausu-tauri": {
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
        binary = tmp_path / "lausu-tauri"
        binary.write_bytes(b"original bytes")
        monkeypatch.setenv(
            "VT_TAURI_MANIFEST",
            _write_manifest(tmp_path, "lausu-tauri", _tauri_manifest_key(), "b" * 64),
        )
        binary.write_bytes(b"tampered bytes")
        assert verify_tauri_binary_or_skip(binary) is False

    def test_binary_read_failure_fails_closed(self, tmp_path, monkeypatch):
        """Binary unreadable at hash time → refuse to spawn (False)."""
        binary = tmp_path / "lausu-tauri"
        # Do NOT create the file, read_bytes() raises FileNotFoundError.
        monkeypatch.setenv(
            "VT_TAURI_MANIFEST",
            _write_manifest(tmp_path, "lausu-tauri", _tauri_manifest_key(), "c" * 64),
        )
        assert verify_tauri_binary_or_skip(binary) is False

    def test_accepts_str_path(self, tmp_path, monkeypatch):
        """The helper accepts a ``str`` path as well as ``Path``."""
        binary = tmp_path / "lausu-tauri"
        binary.write_bytes(b"fake tauri binary bytes")
        sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        monkeypatch.setenv("VT_TAURI_MANIFEST", _write_manifest(tmp_path, "lausu-tauri", _tauri_manifest_key(), sha))
        assert verify_tauri_binary_or_skip(str(binary)) is True


class TestTauriManifestKey:
    """The platform/arch key resolution mirrors the manifest contract."""

    def test_returns_string(self):
        assert isinstance(_tauri_manifest_key(), str)
        assert _tauri_manifest_key()


class TestTauriManifestPathExeAdjacent:
    """Frozen/installed layouts resolve via the exe-adjacent candidates."""

    def _write_manifest_at(self, path: Path, binary_name: str, sha: str) -> None:
        manifest = {
            "version": 1,
            "binaries": {
                binary_name: {
                    "sha256": {_tauri_manifest_key(): sha},
                    "_platforms": [],
                    "_install_paths": [],
                }
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def _isolate_home(self, tmp_path, monkeypatch):
        empty_home = tmp_path / "home_empty"
        empty_home.mkdir(exist_ok=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty_home))

    def test_manifest_found_exe_adjacent_when_repo_root_missing(self, tmp_path, monkeypatch):
        """Exe-dir manifest wins when the __file__ tree has no manifest."""
        monkeypatch.delenv("VT_TAURI_MANIFEST", raising=False)
        self._isolate_home(tmp_path, monkeypatch)
        # Fake __file__ tree with no manifest anywhere above it.
        fake_file = tmp_path / "repo_fake" / "voice_typer" / "server" / "autostart" / "tauri_spawn.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.write_text("# fake", encoding="utf-8")
        monkeypatch.setattr(_tauri_spawn_mod, "__file__", str(fake_file))
        # Exe-adjacent manifest with a matching hash.
        exe_dir = tmp_path / "exe_dir"
        exe_dir.mkdir()
        binary = exe_dir / "lausu-tauri"
        binary.write_bytes(b"exe-adjacent binary bytes")
        sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        self._write_manifest_at(exe_dir / "tauri-binaries.json", "lausu-tauri", sha)
        monkeypatch.setattr(sys, "executable", str(exe_dir / "python-sidecar.exe"))
        assert _tauri_manifest_path() == exe_dir / "tauri-binaries.json"
        assert verify_tauri_binary_or_skip(binary) is True

    def test_frozen_file_tmp_still_resolves_via_exe_resources_dir(self, tmp_path, monkeypatch):
        """Nuitka onefile temp __file__ still resolves via exe resources/."""
        monkeypatch.delenv("VT_TAURI_MANIFEST", raising=False)
        self._isolate_home(tmp_path, monkeypatch)
        # Simulated Nuitka onefile temp extraction path (garbage root).
        fake_file = tmp_path / "_MEI12345" / "voice_typer" / "server" / "autostart" / "tauri_spawn.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.write_text("# fake frozen", encoding="utf-8")
        monkeypatch.setattr(_tauri_spawn_mod, "__file__", str(fake_file))
        # Manifest lives in <exe-dir>/resources/ (installed layout).
        exe_dir = tmp_path / "install"
        resources_dir = exe_dir / "resources"
        resources_dir.mkdir(parents=True)
        binary = exe_dir / "lausu-tauri"
        binary.write_bytes(b"frozen layout binary bytes")
        sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        self._write_manifest_at(resources_dir / "tauri-binaries.json", "lausu-tauri", sha)
        monkeypatch.setattr(sys, "executable", str(exe_dir / "lausu-sidecar.exe"))
        assert _tauri_manifest_path() == resources_dir / "tauri-binaries.json"
        assert verify_tauri_binary_or_skip(binary) is True
