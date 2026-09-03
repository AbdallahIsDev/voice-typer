"""Tests for ``scripts/build/update_native_manifests.py``.

Covers the manifest-update script must (a) hash the binaries
it finds in ``voice_typer/server/native/`` with
``hashlib.sha256(path.read_bytes()).hexdigest()``, (b) write the
sha256 back into ``binaries.json``, (c) update BOTH the arch-suffixed
entry AND its legacy alias (where one exists), and (d) be idempotent.
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

import update_native_manifests as unm  # noqa: E402  (path inserted above)

# ─── Fixtures ────────────────────────────────────────────────────────────


def _seed_manifest(path: Path) -> dict:
    """Write a minimal manifest template to ``path`` and return it."""
    manifest = {
        "_comment": "test manifest",
        "version": 1,
        "binaries": {
            "linux-key-listener-x86_64": {
                "sha256": "",
                "version": "1.0.0",
                "min_proto_version": 1,
            },
            "linux-key-listener-aarch64": {
                "sha256": "",
                "version": "1.0.0",
                "min_proto_version": 1,
            },
            "windows-key-listener-x86_64.exe": {
                "sha256": "",
                "version": "1.0.0",
                "min_proto_version": 1,
            },
            "windows-key-listener-aarch64.exe": {
                "sha256": "",
                "version": "1.0.0",
                "min_proto_version": 1,
            },
            "macos-key-listener": {
                "sha256": "",
                "version": "1.0.0",
                "min_proto_version": 1,
            },
            "linux-key-listener": {
                "sha256": "",
                "version": "1.0.0",
                "min_proto_version": 1,
            },
            "windows-key-listener.exe": {
                "sha256": "",
                "version": "1.0.0",
                "min_proto_version": 1,
            },
        },
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


@pytest.fixture
def native_dir(tmp_path: Path) -> Path:
    """A fresh ``native/`` dir containing a seed manifest only."""
    nd = tmp_path / "native"
    nd.mkdir()
    _seed_manifest(nd / "binaries.json")
    return nd


def _write_binary(native_dir: Path, name: str, content: bytes) -> Path:
    p = native_dir / name
    p.write_bytes(content)
    return p


# ─── Tests ───────────────────────────────────────────────────────────────


def test_sha256_matches_hashlib(native_dir: Path) -> None:
    """The recorded sha256 MUST equal ``hashlib.sha256(bytes).hexdigest()``."""
    bin_path = _write_binary(native_dir, "linux-key-listener", b"hello world\n")
    expected = hashlib.sha256(b"hello world\n").hexdigest()

    updated = unm.update_manifest(native_dir)
    assert updated["linux-key-listener"] == expected
    assert updated["linux-key-listener-x86_64"] == expected  # legacy alias

    manifest = json.loads((native_dir / "binaries.json").read_text())
    assert manifest["binaries"]["linux-key-listener"]["sha256"] == expected
    assert manifest["binaries"]["linux-key-listener-x86_64"]["sha256"] == expected
    # The hash on disk MUST match what we'd compute manually.
    assert unm._sha256_of(bin_path) == expected


def test_legacy_alias_is_updated(native_dir: Path) -> None:
    """A binary emitted under its legacy name updates BOTH alias entries."""
    _write_binary(native_dir, "linux-key-listener", b"linux-x86_64-payload")
    expected = hashlib.sha256(b"linux-x86_64-payload").hexdigest()

    unm.update_manifest(native_dir)

    manifest = json.loads((native_dir / "binaries.json").read_text())
    # Direct (legacy) name.
    assert manifest["binaries"]["linux-key-listener"]["sha256"] == expected
    # x86_64 arch-suffixed alias.
    assert manifest["binaries"]["linux-key-listener-x86_64"]["sha256"] == expected
    # aarch64 entry is NOT touched (no legacy equivalent for aarch64).
    assert manifest["binaries"]["linux-key-listener-aarch64"]["sha256"] == ""


def test_windows_legacy_alias_updated(native_dir: Path) -> None:
    """``windows-key-listener.exe`` (legacy) updates its x86_64 alias too."""
    _write_binary(native_dir, "windows-key-listener.exe", b"win-x86_64-payload")
    expected = hashlib.sha256(b"win-x86_64-payload").hexdigest()

    unm.update_manifest(native_dir)

    manifest = json.loads((native_dir / "binaries.json").read_text())
    assert manifest["binaries"]["windows-key-listener.exe"]["sha256"] == expected
    assert manifest["binaries"]["windows-key-listener-x86_64.exe"]["sha256"] == expected
    # aarch64 entry is NOT touched.
    assert manifest["binaries"]["windows-key-listener-aarch64.exe"]["sha256"] == ""


def test_arch_suffixed_binary_updates_legacy_alias(native_dir: Path) -> None:
    """A binary emitted under its arch-suffixed name also updates the legacy alias."""
    _write_binary(native_dir, "linux-key-listener-x86_64", b"arch-suffixed-payload")
    expected = hashlib.sha256(b"arch-suffixed-payload").hexdigest()

    unm.update_manifest(native_dir)

    manifest = json.loads((native_dir / "binaries.json").read_text())
    assert manifest["binaries"]["linux-key-listener-x86_64"]["sha256"] == expected
    assert manifest["binaries"]["linux-key-listener"]["sha256"] == expected


def test_macos_single_entry(native_dir: Path) -> None:
    """``macos-key-listener`` has NO alias — only its own entry is touched."""
    _write_binary(native_dir, "macos-key-listener", b"universal-binary")
    expected = hashlib.sha256(b"universal-binary").hexdigest()

    unm.update_manifest(native_dir)

    manifest = json.loads((native_dir / "binaries.json").read_text())
    assert manifest["binaries"]["macos-key-listener"]["sha256"] == expected
    # No other entries should have been touched.
    for name, entry in manifest["binaries"].items():
        if name != "macos-key-listener":
            assert entry["sha256"] == "", f"{name} was unexpectedly modified"


def test_idempotent(native_dir: Path) -> None:
    """Running twice produces the same manifest content."""
    _write_binary(native_dir, "linux-key-listener", b"payload-v1")
    unm.update_manifest(native_dir)
    first = (native_dir / "binaries.json").read_text()

    unm.update_manifest(native_dir)
    second = (native_dir / "binaries.json").read_text()

    assert first == second


def test_preserves_version_and_min_proto_version(native_dir: Path) -> None:
    """Only ``sha256`` is rewritten; ``version`` + ``min_proto_version`` are preserved."""
    # Customize the version + min_proto_version on the linux-x86_64 entry.
    manifest = json.loads((native_dir / "binaries.json").read_text())
    manifest["binaries"]["linux-key-listener-x86_64"]["version"] = "9.9.9"
    manifest["binaries"]["linux-key-listener-x86_64"]["min_proto_version"] = 42
    (native_dir / "binaries.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    _write_binary(native_dir, "linux-key-listener", b"payload")
    unm.update_manifest(native_dir)

    after = json.loads((native_dir / "binaries.json").read_text())
    entry = after["binaries"]["linux-key-listener-x86_64"]
    assert entry["version"] == "9.9.9"
    assert entry["min_proto_version"] == 42
    # sha256 was still updated.
    assert entry["sha256"] == hashlib.sha256(b"payload").hexdigest()


def test_preserves_comment_and_top_level_version(native_dir: Path) -> None:
    """The ``_comment`` and top-level ``version`` keys round-trip verbatim."""
    _write_binary(native_dir, "linux-key-listener", b"payload")
    before = json.loads((native_dir / "binaries.json").read_text())
    unm.update_manifest(native_dir)
    after = json.loads((native_dir / "binaries.json").read_text())

    assert after["_comment"] == before["_comment"]
    assert after["version"] == before["version"]


def test_skips_unknown_files(native_dir: Path) -> None:
    """Files in native/ that aren't known binaries are silently ignored."""
    # A source file that lives alongside the binaries.
    (native_dir / "linux-key-listener.c").write_text("// not a binary\n")
    # An unrelated README.
    (native_dir / "README.md").write_text("docs only")
    _write_binary(native_dir, "linux-key-listener", b"payload")

    unm.update_manifest(native_dir)

    manifest = json.loads((native_dir / "binaries.json").read_text())
    assert manifest["binaries"]["linux-key-listener"]["sha256"] != ""
    # No new keys were added for the .c / .md files.
    assert "linux-key-listener.c" not in manifest["binaries"]
    assert "README.md" not in manifest["binaries"]


def test_no_binaries_leaves_manifest_untouched(native_dir: Path) -> None:
    """If no known binaries are present, the manifest is unchanged."""
    before = (native_dir / "binaries.json").read_text()
    updated = unm.update_manifest(native_dir)
    after = (native_dir / "binaries.json").read_text()

    assert updated == {}
    assert before == after


def test_multiple_binaries_all_updated(native_dir: Path) -> None:
    """A mixed-OS native/ dir updates every binary it contains."""
    _write_binary(native_dir, "linux-key-listener", b"linux-payload")
    _write_binary(native_dir, "windows-key-listener.exe", b"windows-payload")
    _write_binary(native_dir, "macos-key-listener", b"macos-payload")

    unm.update_manifest(native_dir)

    manifest = json.loads((native_dir / "binaries.json").read_text())
    assert manifest["binaries"]["linux-key-listener"]["sha256"] == hashlib.sha256(b"linux-payload").hexdigest()
    assert manifest["binaries"]["linux-key-listener-x86_64"]["sha256"] == hashlib.sha256(b"linux-payload").hexdigest()
    assert manifest["binaries"]["windows-key-listener.exe"]["sha256"] == hashlib.sha256(b"windows-payload").hexdigest()
    assert (
        manifest["binaries"]["windows-key-listener-x86_64.exe"]["sha256"]
        == hashlib.sha256(b"windows-payload").hexdigest()
    )
    assert manifest["binaries"]["macos-key-listener"]["sha256"] == hashlib.sha256(b"macos-payload").hexdigest()


def test_aarch64_binary_does_not_update_legacy(native_dir: Path) -> None:
    """aarch64 arch-suffixed names have NO legacy alias — only direct entry."""
    _write_binary(native_dir, "linux-key-listener-aarch64", b"aarch64-payload")
    expected = hashlib.sha256(b"aarch64-payload").hexdigest()

    unm.update_manifest(native_dir)

    manifest = json.loads((native_dir / "binaries.json").read_text())
    assert manifest["binaries"]["linux-key-listener-aarch64"]["sha256"] == expected
    # Legacy ``linux-key-listener`` (always x86_64) MUST NOT be touched
    # by an aarch64 build — that would let an aarch64 binary satisfy
    # verification for an x86_64 host.
    assert manifest["binaries"]["linux-key-listener"]["sha256"] == ""
    assert manifest["binaries"]["linux-key-listener-x86_64"]["sha256"] == ""


def test_main_returns_zero_on_success(native_dir: Path) -> None:
    """The CLI ``main`` exits 0 when there's at least one binary to hash."""
    _write_binary(native_dir, "linux-key-listener", b"payload")
    rc = unm.main(["update_native_manifests.py", str(native_dir)])
    assert rc == 0


def test_main_returns_one_when_native_dir_missing(tmp_path: Path) -> None:
    rc = unm.main(["update_native_manifests.py", str(tmp_path / "nope")])
    assert rc == 1


def test_main_returns_one_when_manifest_missing(tmp_path: Path) -> None:
    """If binaries.json is absent, the script errors out (fail-closed)."""
    nd = tmp_path / "native"
    nd.mkdir()
    rc = unm.main(["update_native_manifests.py", str(nd)])
    assert rc == 1


def test_main_returns_one_on_too_many_args(native_dir: Path) -> None:
    rc = unm.main(["update_native_manifests.py", str(native_dir), "extra"])
    assert rc == 1


# ─── sha256_by_arch sync (legacy entries) ─────────────────────────────────


class TestSha256ByArchSync:
    """Legacy manifest entries carry a per-arch ``sha256_by_arch`` dict
    (see the schema in ``binaries.json``). ``update_manifest`` must keep
    the dict in sync for the arch the build ran on — otherwise every
    manifest regen moves the flat ``sha256`` forward while the per-arch
    hash stays stale, breaking the schema invariant
    (``sha256 == sha256_by_arch.x86_64`` on an x86_64 tree) that the
    checksum tests pin."""

    @staticmethod
    def _seed_manifest_with_by_arch(path: Path) -> dict[str, str]:
        """Manifest whose legacy entries carry a ``sha256_by_arch`` dict.

        The seed is HOST-AGNOSTIC: the host arch starts STALE (``old``)
        so the lockstep test can prove it MOVES to the new hash, while
        the non-built arch starts EMPTY (the dev-tree state) so the
        not-fabricated assertion (``== ""``) holds on every CI runner —
        x86_64 AND aarch64. Seeding ``old`` unconditionally for x86_64
        broke the test on aarch64 hosts (the stale ``old`` landed on the
        NON-built arch and the ``== ""`` assertion failed).

        Returns the seeded ``sha256_by_arch`` dict so callers can assert
        against the exact pre-update state.
        """
        host_arch = unm._host_arch_key()
        assert host_arch in ("x86_64", "aarch64"), f"test expects a recognized host arch; got {host_arch!r}"
        other_arch = "aarch64" if host_arch == "x86_64" else "x86_64"
        by_arch = {host_arch: "old", other_arch: ""}
        manifest = {
            "_comment": "test manifest",
            "version": 1,
            "binaries": {
                "linux-key-listener-x86_64": {
                    "sha256": "old",
                    "version": "1.0.0",
                    "min_proto_version": 1,
                },
                "linux-key-listener": {
                    "sha256": "old",
                    "sha256_by_arch": by_arch,
                    "version": "1.0.0",
                    "min_proto_version": 1,
                },
            },
        }
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return by_arch

    def test_host_arch_entry_updated_in_lockstep_with_flat(self, tmp_path: Path) -> None:
        """A rebuilt legacy binary updates flat sha256 AND the host-arch
        ``sha256_by_arch`` entry (and leaves the other arch untouched)."""
        nd = tmp_path / "native"
        nd.mkdir()
        self._seed_manifest_with_by_arch(nd / "binaries.json")
        _write_binary(nd, "linux-key-listener", b"new-payload")
        expected = hashlib.sha256(b"new-payload").hexdigest()

        unm.update_manifest(nd)

        manifest = json.loads((nd / "binaries.json").read_text())
        entry = manifest["binaries"]["linux-key-listener"]
        assert entry["sha256"] == expected
        host_arch = unm._host_arch_key()
        assert host_arch in ("x86_64", "aarch64"), f"test expects a recognized host arch; got {host_arch!r}"
        assert entry["sha256_by_arch"][host_arch] == expected, (
            f"sha256_by_arch.{host_arch} must move in lockstep with the flat "
            "sha256 when the binary is rebuilt on this arch"
        )
        other = "aarch64" if host_arch == "x86_64" else "x86_64"
        assert entry["sha256_by_arch"][other] == "", (
            f"the non-built arch ({other}) must not be fabricated by an update on this host"
        )
        # The arch-suffixed alias entry has no by-arch dict — untouched shape.
        assert "sha256_by_arch" not in manifest["binaries"]["linux-key-listener-x86_64"]
        assert manifest["binaries"]["linux-key-listener-x86_64"]["sha256"] == expected

    def test_unknown_machine_leaves_by_arch_untouched(self, tmp_path: Path, monkeypatch) -> None:
        """An unrecognized ``platform.machine()`` must not corrupt the
        ``sha256_by_arch`` dict — only the flat field moves on."""
        nd = tmp_path / "native"
        nd.mkdir()
        seeded_by_arch = self._seed_manifest_with_by_arch(nd / "binaries.json")
        _write_binary(nd, "linux-key-listener", b"odd-host-payload")
        expected = hashlib.sha256(b"odd-host-payload").hexdigest()

        monkeypatch.setattr(unm.platform, "machine", lambda: "riscv64")
        assert unm._host_arch_key() is None

        unm.update_manifest(nd)

        manifest = json.loads((nd / "binaries.json").read_text())
        entry = manifest["binaries"]["linux-key-listener"]
        assert entry["sha256"] == expected
        assert entry["sha256_by_arch"] == seeded_by_arch, "unrecognized host arch must leave sha256_by_arch untouched"

    def test_host_arch_key_normalization(self, monkeypatch) -> None:
        """machine() strings normalize to the manifest's arch keys."""
        for machine, expected in (
            ("x86_64", "x86_64"),
            ("AMD64", "x86_64"),
            ("aarch64", "aarch64"),
            ("arm64", "aarch64"),
        ):
            monkeypatch.setattr(unm.platform, "machine", lambda m=machine: m)
            assert unm._host_arch_key() == expected, f"{machine} → {expected}"
        monkeypatch.setattr(unm.platform, "machine", lambda: "sparc")
        assert unm._host_arch_key() is None
