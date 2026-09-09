"""Install stage for the downloaded runtime pack (plan §8.3 + §4.6).

The download stage (`download_offline_pack_with_resume`) leaves a
verified ``pack-<version>.partial`` archive on disk and RETURNS — this
file covers the missing link: `install_offline_pack` extracts the
archive into a ``<version>.new`` staging dir, verifies every file
against the manifest, writes ``pack-manifest.json``, and atomically
swaps the staging dir into place.

The runtime-pack worker start step is intentionally NOT covered here:
that step is owned by the host-side worker wiring decision and is out
of scope for the install stage (the pack is installed and ready).

Tested behaviors:

  1. Happy path: fixture pack → extracted at ``<root>/<version>/``,
     manifest written, archive deleted, ``offline_pack_verified``
     event published with ``{version, sha256}`` (renderer contract).
  2. The cheap existence probe + full verification both succeed after
     install (the launch-time scan finds the pack).
  3. An existing pack dir is replaced (stale files gone) — the swap
     path.
  4. A missing archive → False (nothing to install — the download
     fake wrote nothing).
  5. A tampered inner file → fail closed BEFORE the swap: no version
     dir, staging cleaned, archive KEPT for retry, ``offline_pack_corrupt``
     event.
  6. A non-zip archive → False, staging cleaned, archive kept.
  7. A zip-slip entry (``../escape``) → rejected, nothing written
     outside staging.
  8. Swap failure → False, staging cleaned, archive kept,
     ``offline_pack_download_failed`` with reason ``install_failed``.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from voice_typer.server.service import offline_pack


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _build_fixture_pack(
    tmp_path: Path, version: str = "1.2.3", *, archive_name: str | None = None
) -> tuple[Path, dict]:
    """Build a fixture pack archive + its manifest dict.

    Mirrors the release layout from ``publish_pack_release.py`` /
    ``full-offline-installer.nsi``: ``pack-<version>.zip`` entries
    extract DIRECTLY into the pack dir (no top-level folder), and the
    manifest is a separate dict describing each file.
    """
    files = {
        "worker.exe": b"worker-binary-blob",
        "engines/parakeet.onnx": b"onnx-weights-blob",
    }
    archive = tmp_path / (archive_name or f"pack-{version}.partial")
    with zipfile.ZipFile(archive, "w") as zf:
        for name, blob in files.items():
            zf.writestr(name, blob)
    manifest = {
        "version": version,
        "sha256": _sha256_bytes(archive.read_bytes()),
        "files": [{"name": name, "sha256": _sha256_bytes(blob), "size": len(blob)} for name, blob in files.items()],
        "min_proto_version": 1,
    }
    return archive, manifest


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:
        self.events.append(event)

    def of_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e["type"] == event_type]


class TestInstallOfflinePack:
    """``install_offline_pack`` — extract → verify → manifest → swap."""

    def test_happy_path_installs_pack(self, tmp_path: Path):
        archive, manifest = _build_fixture_pack(tmp_path)
        bus = _FakeBus()
        version = manifest["version"]

        ok = offline_pack.install_offline_pack(archive, version, manifest, root=tmp_path, event_bus=bus)

        assert ok is True
        pack_dir = tmp_path / version
        # Extracted files are at the version dir (no top-level folder).
        assert (pack_dir / "worker.exe").read_bytes() == b"worker-binary-blob"
        assert (pack_dir / "engines" / "parakeet.onnx").read_bytes() == b"onnx-weights-blob"
        # Manifest written + structurally valid.
        written = json.loads((pack_dir / "pack-manifest.json").read_text(encoding="utf-8"))
        assert written["version"] == version
        assert written["sha256"] == manifest["sha256"]
        # The consumed archive is removed (mirrors the NSIS installer).
        assert not archive.exists()
        # Launch-time probes succeed on the installed pack.
        assert offline_pack.offline_pack_exists(version, root=tmp_path) is True
        assert offline_pack.verify_offline_pack_or_skip(version, root=tmp_path) is True
        # Renderer contract: offline_pack_verified {version, sha256}.
        verified = bus.of_type("offline_pack_verified")
        assert verified and verified[0]["data"] == {
            "version": version,
            "sha256": manifest["sha256"],
        }

    def test_install_replaces_existing_pack_dir(self, tmp_path: Path):
        """An existing (older) pack dir is swapped away — stale files gone."""
        archive, manifest = _build_fixture_pack(tmp_path, "2.0.0")
        pack_dir = tmp_path / "2.0.0"
        pack_dir.mkdir()
        (pack_dir / "stale-old-worker.exe").write_bytes(b"old")

        ok = offline_pack.install_offline_pack(archive, "2.0.0", manifest, root=tmp_path)

        assert ok is True
        assert not (pack_dir / "stale-old-worker.exe").exists()
        assert (pack_dir / "worker.exe").exists()

    def test_missing_archive_returns_false(self, tmp_path: Path):
        """Nothing was downloaded (e.g. an injected fake wrote no file) —
        the install stage is a no-op, not an error."""
        _archive, manifest = _build_fixture_pack(tmp_path, "1.0.0")
        missing = tmp_path / "pack-1.0.0.partial"
        missing.unlink()

        ok = offline_pack.install_offline_pack(missing, "1.0.0", manifest, root=tmp_path, event_bus=_FakeBus())

        assert ok is False
        assert not (tmp_path / "1.0.0").exists()

    def test_tampered_inner_file_fails_closed_before_swap(self, tmp_path: Path):
        """A manifest/zip disagreement (tampered inner file) → fail closed
        BEFORE the swap: no version dir, staging cleaned, archive kept
        for retry, ``offline_pack_corrupt`` published."""
        archive, manifest = _build_fixture_pack(tmp_path, "1.2.3")
        # Tamper: manifest declares a hash the zip does not contain.
        manifest["files"][0]["sha256"] = "0" * 64
        bus = _FakeBus()

        ok = offline_pack.install_offline_pack(archive, "1.2.3", manifest, root=tmp_path, event_bus=bus)

        assert ok is False
        assert not (tmp_path / "1.2.3").exists()
        assert not Path(str(tmp_path / "1.2.3") + ".new").exists()
        # Partial kept for retry.
        assert archive.exists()
        corrupt = bus.of_type("offline_pack_corrupt")
        assert corrupt and corrupt[0]["data"]["version"] == "1.2.3"

    def test_bad_zip_archive_returns_false(self, tmp_path: Path):
        archive = tmp_path / "pack-3.0.0.partial"
        archive.write_bytes(b"this is not a zip file")
        manifest = {
            "version": "3.0.0",
            "sha256": _sha256_bytes(b"this is not a zip file"),
            "files": [{"name": "worker.exe", "sha256": _sha256_bytes(b"x"), "size": 1}],
            "min_proto_version": 1,
        }

        ok = offline_pack.install_offline_pack(archive, "3.0.0", manifest, root=tmp_path, event_bus=_FakeBus())

        assert ok is False
        assert not (tmp_path / "3.0.0").exists()
        assert not Path(str(tmp_path / "3.0.0") + ".new").exists()
        # Partial kept for retry.
        assert archive.exists()

    def test_zip_slip_entry_rejected(self, tmp_path: Path):
        """A zip entry escaping the staging dir (zip-slip) is rejected —
        nothing is written outside staging."""
        archive = tmp_path / "pack-4.0.0.partial"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escaped.txt", b"evil")
        manifest = {
            "version": "4.0.0",
            "sha256": _sha256_bytes(archive.read_bytes()),
            "files": [],
            "min_proto_version": 1,
        }

        ok = offline_pack.install_offline_pack(archive, "4.0.0", manifest, root=tmp_path, event_bus=_FakeBus())

        assert ok is False
        assert not (tmp_path / "escaped.txt").exists()
        assert not (tmp_path / "4.0.0").exists()

    def test_swap_failure_cleans_staging_and_keeps_partial(self, tmp_path: Path, monkeypatch):
        """When the atomic swap raises (e.g. the Windows worker exe is
        open), the staging dir is cleaned, the partial is kept, and the
        failure is reported via ``offline_pack_download_failed``."""
        archive, manifest = _build_fixture_pack(tmp_path, "5.0.0")
        bus = _FakeBus()

        def boom(new_dir, current_dir, **kwargs):
            raise OSError("destination open by another process")

        monkeypatch.setattr(offline_pack, "atomic_swap_offline_pack", boom)

        ok = offline_pack.install_offline_pack(archive, "5.0.0", manifest, root=tmp_path, event_bus=bus)

        assert ok is False
        assert not Path(str(tmp_path / "5.0.0") + ".new").exists()
        assert archive.exists()
        failed = bus.of_type("offline_pack_download_failed")
        assert failed and failed[0]["data"]["reason"] == "install_failed"

    def test_stale_staging_dir_is_discarded(self, tmp_path: Path):
        """A leftover ``<version>.new`` from a previous failed install is
        removed before extraction (never merged with the new one)."""
        archive, manifest = _build_fixture_pack(tmp_path, "6.0.0")
        stale = Path(str(tmp_path / "6.0.0") + ".new")
        stale.mkdir(parents=True)
        (stale / "leftover.bin").write_bytes(b"stale")

        ok = offline_pack.install_offline_pack(archive, "6.0.0", manifest, root=tmp_path)

        assert ok is True
        pack_dir = tmp_path / "6.0.0"
        assert not (pack_dir / "leftover.bin").exists()
        assert (pack_dir / "worker.exe").exists()

    def test_unsafe_manifest_entry_name_fails_closed(self, tmp_path: Path):
        """A manifest entry whose name escapes the pack dir is rejected
        (the manifest comes over the network — never trust path parts)."""
        archive, manifest = _build_fixture_pack(tmp_path, "7.0.0")
        manifest["files"][0]["name"] = "../outside.exe"

        ok = offline_pack.install_offline_pack(archive, "7.0.0", manifest, root=tmp_path)

        assert ok is False
        assert not (tmp_path / "outside.exe").exists()
        assert not (tmp_path / "7.0.0").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
