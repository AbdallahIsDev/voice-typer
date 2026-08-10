"""Tests for ``scripts/build/update_tauri_manifests.py``.

Covers the manifest-update script must (a) hash the cargo-built
``voice-typer-tauri`` binary with
``hashlib.sha256(path.read_bytes()).hexdigest()``, (b) write the hash
into the per-arch ``sha256`` sub-key that belongs to the given target
triple (leaving every other key untouched), (c) discover the binary per
OS (linux/windows release dir, macOS ``.app`` bundle inner executable),
(d) be idempotent, and (e) fail closed on the ``--check`` integrity
gate (empty or malformed sub-keys).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

# Make scripts/build importable as a module path.
_SCRIPTS_BUILD = Path(__file__).resolve().parent.parent / "scripts" / "build"
if str(_SCRIPTS_BUILD) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_BUILD))

import update_tauri_manifests as utm  # noqa: E402  (path inserted above)


def _seed_manifest(path: Path) -> dict:
    """Write a minimal manifest to ``path`` matching the real schema."""
    manifest = {
        "_comment": "test manifest",
        "version": 1,
        "binaries": {
            "voice-typer-tauri": {
                "sha256": {"linux-x86_64": "", "linux-aarch64": ""},
                "version": "1.0.0",
                "min_proto_version": 1,
                "_platforms": ["linux-x86_64", "linux-aarch64"],
                "_install_paths": ["/usr/bin/voice-typer-tauri"],
            },
            "voice-typer-tauri.exe": {
                "sha256": {"windows-x86_64": "", "windows-aarch64": ""},
                "version": "1.0.0",
                "min_proto_version": 1,
            },
            "voice-typer-tauri.app": {
                "sha256": {"macos": ""},
                "version": "1.0.0",
                "min_proto_version": 1,
            },
        },
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    """A fresh manifest path with the seed manifest."""
    mp = tmp_path / "tauri-binaries.json"
    _seed_manifest(mp)
    return mp


def _write_binary(target_dir: Path, triple: str, content: bytes) -> Path:
    """Write a linux/windows-style binary and return its path."""
    rel = target_dir / triple / "release"
    rel.mkdir(parents=True)
    name = "voice-typer-tauri" + (".exe" if "pc-windows" in triple else "")
    p = rel / name
    p.write_bytes(content)
    return p


def _write_macos_app(target_dir: Path, triple: str, content: bytes) -> Path:
    """Write a macOS ``.app`` bundle layout and return the inner executable."""
    exe = (
        target_dir
        / triple
        / "release"
        / "bundle"
        / "macos"
        / "Voice Typer.app"
        / "Contents"
        / "MacOS"
        / "voice-typer-tauri"
    )
    exe.parent.mkdir(parents=True)
    exe.write_bytes(content)
    return exe


# ─── record mode ───────────────────────────────────────────────────────────


def test_record_linux_hashes_into_linux_x86_64_key(manifest_path: Path, tmp_path: Path) -> None:
    target = tmp_path / "target"
    binary = _write_binary(target, "x86_64-unknown-linux-gnu", b"linux payload\n")
    expected = hashlib.sha256(b"linux payload\n").hexdigest()

    sha = utm.record_sha256(manifest_path, target, "x86_64-unknown-linux-gnu")

    assert sha == expected
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["binaries"]["voice-typer-tauri"]["sha256"]
    assert entry["linux-x86_64"] == expected
    # Other platforms' keys untouched.
    assert entry["linux-aarch64"] == ""
    assert manifest["binaries"]["voice-typer-tauri.exe"]["sha256"]["windows-x86_64"] == ""
    assert manifest["binaries"]["voice-typer-tauri.app"]["sha256"]["macos"] == ""
    # The hash on disk MUST equal what we compute manually.
    assert utm._sha256_of(binary) == expected


def test_record_windows_hashes_exe_key(manifest_path: Path, tmp_path: Path) -> None:
    target = tmp_path / "target"
    _write_binary(target, "x86_64-pc-windows-msvc", b"win payload")
    expected = hashlib.sha256(b"win payload").hexdigest()

    utm.record_sha256(manifest_path, target, "x86_64-pc-windows-msvc")

    manifest = json.loads(manifest_path.read_text())
    assert manifest["binaries"]["voice-typer-tauri.exe"]["sha256"]["windows-x86_64"] == expected
    assert manifest["binaries"]["voice-typer-tauri.exe"]["sha256"]["windows-aarch64"] == ""


def test_record_macos_discovers_app_inner_executable(manifest_path: Path, tmp_path: Path) -> None:
    target = tmp_path / "target"
    _write_macos_app(target, "universal-apple-darwin", b"universal mach-o")
    expected = hashlib.sha256(b"universal mach-o").hexdigest()

    utm.record_sha256(manifest_path, target, "universal-apple-darwin")

    manifest = json.loads(manifest_path.read_text())
    assert manifest["binaries"]["voice-typer-tauri.app"]["sha256"]["macos"] == expected


def test_record_macos_ambiguous_app_bundles_raise(manifest_path: Path, tmp_path: Path) -> None:
    target = tmp_path / "target"
    for app in ("Voice Typer.app", "Other.app"):
        exe = (
            target
            / "aarch64-apple-darwin"
            / "release"
            / "bundle"
            / "macos"
            / app
            / "Contents"
            / "MacOS"
            / "voice-typer-tauri"
        )
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"x")
    with pytest.raises(ValueError, match="ambiguous macOS discovery"):
        utm.record_sha256(manifest_path, target, "aarch64-apple-darwin")


def test_record_with_explicit_binary(manifest_path: Path, tmp_path: Path) -> None:
    binary = tmp_path / "custom-place" / "voice-typer-tauri"
    binary.parent.mkdir()
    binary.write_bytes(b"custom path payload")
    expected = hashlib.sha256(b"custom path payload").hexdigest()

    utm.record_sha256(manifest_path, tmp_path / "target", "aarch64-unknown-linux-gnu", binary=binary)

    manifest = json.loads(manifest_path.read_text())
    assert manifest["binaries"]["voice-typer-tauri"]["sha256"]["linux-aarch64"] == expected


def test_record_missing_binary_raises_file_not_found(manifest_path: Path, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        utm.record_sha256(manifest_path, tmp_path / "target", "x86_64-unknown-linux-gnu")


def test_record_unknown_triple_raises_key_error(manifest_path: Path, tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        utm.record_sha256(manifest_path, tmp_path / "target", "riscv64-unknown-linux-gnu")


def test_record_preserves_other_fields(manifest_path: Path, tmp_path: Path) -> None:
    _write_binary(tmp_path / "target", "x86_64-unknown-linux-gnu", b"payload")
    before = json.loads(manifest_path.read_text())
    utm.record_sha256(manifest_path, tmp_path / "target", "x86_64-unknown-linux-gnu")
    after = json.loads(manifest_path.read_text())

    entry = after["binaries"]["voice-typer-tauri"]
    assert entry["version"] == "1.0.0"
    assert entry["min_proto_version"] == 1
    assert entry["_install_paths"] == ["/usr/bin/voice-typer-tauri"]
    assert entry["_platforms"] == ["linux-x86_64", "linux-aarch64"]
    assert after["_comment"] == before["_comment"]
    assert after["version"] == before["version"]


def test_record_idempotent(manifest_path: Path, tmp_path: Path) -> None:
    _write_binary(tmp_path / "target", "x86_64-unknown-linux-gnu", b"payload v1")
    utm.record_sha256(manifest_path, tmp_path / "target", "x86_64-unknown-linux-gnu")
    first = manifest_path.read_text()
    utm.record_sha256(manifest_path, tmp_path / "target", "x86_64-unknown-linux-gnu")
    assert manifest_path.read_text() == first


# ─── check mode ────────────────────────────────────────────────────────────


def test_check_scoped_to_triple_ignores_other_empty_keys(manifest_path: Path, tmp_path: Path) -> None:
    _write_binary(tmp_path / "target", "x86_64-unknown-linux-gnu", b"payload")
    utm.record_sha256(manifest_path, tmp_path / "target", "x86_64-unknown-linux-gnu")
    # linux-aarch64 / windows-* / macos still empty — the x86_64 leg's
    # enforce step must PASS anyway (other legs fill their own keys).
    assert utm.check_manifest(manifest_path, triple="x86_64-unknown-linux-gnu") == []


def test_check_scoped_fails_on_own_empty_key(manifest_path: Path) -> None:
    violations = utm.check_manifest(manifest_path, triple="x86_64-unknown-linux-gnu")
    assert violations, "scoped check must reject an empty own-key"
    assert "linux-x86_64" in violations[0]


def test_check_full_rejects_any_empty_key(manifest_path: Path) -> None:
    violations = utm.check_manifest(manifest_path)
    assert len(violations) == 5  # all five per-arch sub-keys are empty


def test_check_full_passes_when_all_keys_populated(manifest_path: Path, tmp_path: Path) -> None:
    payloads = {
        "x86_64-unknown-linux-gnu": b"l1",
        "aarch64-unknown-linux-gnu": b"l2",
        "x86_64-pc-windows-msvc": b"w1",
        "aarch64-pc-windows-msvc": b"w2",
        "universal-apple-darwin": b"m1",
    }
    for triple, content in payloads.items():
        _write_binary(tmp_path / "target", triple, content)
    _write_macos_app(tmp_path / "target", "universal-apple-darwin", payloads["universal-apple-darwin"])
    for triple in payloads:
        utm.record_sha256(manifest_path, tmp_path / "target", triple)
    assert utm.check_manifest(manifest_path) == []


def test_check_rejects_malformed_hex(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text())
    manifest["binaries"]["voice-typer-tauri"]["sha256"]["linux-x86_64"] = "ABC"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    violations = utm.check_manifest(manifest_path, triple="x86_64-unknown-linux-gnu")
    assert violations


# ─── CLI main() ────────────────────────────────────────────────────────────


def test_main_record_returns_zero(manifest_path: Path, tmp_path: Path) -> None:
    _write_binary(tmp_path / "target", "x86_64-unknown-linux-gnu", b"payload")
    rc = utm.main(
        [
            "update_tauri_manifests.py",
            "--manifest",
            str(manifest_path),
            "--triple",
            "x86_64-unknown-linux-gnu",
            "--target-dir",
            str(tmp_path / "target"),
        ]
    )
    assert rc == 0


def test_main_record_missing_binary_returns_one(manifest_path: Path, tmp_path: Path) -> None:
    rc = utm.main(
        [
            "update_tauri_manifests.py",
            "--manifest",
            str(manifest_path),
            "--triple",
            "x86_64-unknown-linux-gnu",
            "--target-dir",
            str(tmp_path / "target"),
        ]
    )
    assert rc == 1


def test_main_record_without_triple_returns_one(manifest_path: Path) -> None:
    rc = utm.main(["update_tauri_manifests.py", "--manifest", str(manifest_path)])
    assert rc == 1


def test_main_check_scoped_passes_after_record(manifest_path: Path, tmp_path: Path) -> None:
    _write_binary(tmp_path / "target", "x86_64-unknown-linux-gnu", b"payload")
    utm.main(
        [
            "update_tauri_manifests.py",
            "--manifest",
            str(manifest_path),
            "--triple",
            "x86_64-unknown-linux-gnu",
            "--target-dir",
            str(tmp_path / "target"),
        ]
    )
    rc = utm.main(
        [
            "update_tauri_manifests.py",
            "--manifest",
            str(manifest_path),
            "--check",
            "--triple",
            "x86_64-unknown-linux-gnu",
        ]
    )
    assert rc == 0


def test_main_check_fails_on_unpopulated_manifest(manifest_path: Path) -> None:
    rc = utm.main(
        [
            "update_tauri_manifests.py",
            "--manifest",
            str(manifest_path),
            "--check",
            "--triple",
            "x86_64-unknown-linux-gnu",
        ]
    )
    assert rc == 1


def test_main_full_check_fails_on_partial_manifest(manifest_path: Path, tmp_path: Path) -> None:
    _write_binary(tmp_path / "target", "x86_64-unknown-linux-gnu", b"payload")
    utm.main(
        [
            "update_tauri_manifests.py",
            "--manifest",
            str(manifest_path),
            "--triple",
            "x86_64-unknown-linux-gnu",
            "--target-dir",
            str(tmp_path / "target"),
        ]
    )
    # Only one key populated → the full-manifest release gate must fail.
    rc = utm.main(["update_tauri_manifests.py", "--manifest", str(manifest_path), "--check"])
    assert rc == 1


def test_main_missing_manifest_returns_one(tmp_path: Path) -> None:
    rc = utm.main(
        [
            "update_tauri_manifests.py",
            "--manifest",
            str(tmp_path / "nope.json"),
            "--triple",
            "x86_64-unknown-linux-gnu",
        ]
    )
    assert rc == 1
