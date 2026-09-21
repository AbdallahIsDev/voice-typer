"""§8.10: Pack missing on launch: cheap existence check + background checksum."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import voice_typer.server.startup_tasks as startup_tasks
from voice_typer.server.service import offline_pack, update_check


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write_valid_pack(tmp_path: Path, version: str = "v1") -> Path:
    root = tmp_path / version
    root.mkdir(parents=True)
    worker = root / "worker.exe"
    worker.write_bytes(b"worker-binary-content")
    vad = root / "silero_vad.onnx"
    vad.write_bytes(b"vad-onnx-blob")
    manifest = {
        "version": version,
        "sha256": _sha256(b"aggregate-hash-placeholder"),
        "files": [
            {"name": "worker.exe", "sha256": _sha256(worker.read_bytes()), "size": len(worker.read_bytes())},
            {"name": "silero_vad.onnx", "sha256": _sha256(vad.read_bytes()), "size": len(vad.read_bytes())},
        ],
        "min_proto_version": 1,
    }
    (root / "pack-manifest.json").write_text(json.dumps(manifest))
    return root


class TestPackExists:
    """§8.10, cheap existence check (no hashing)."""

    def test_present_pack_returns_true(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        assert offline_pack.offline_pack_exists("v1", root=tmp_path) is True

    def test_missing_manifest_returns_false(self, tmp_path: Path):
        (tmp_path / "v1").mkdir()  # dir exists, no manifest
        assert offline_pack.offline_pack_exists("v1", root=tmp_path) is False

    def test_missing_file_returns_false(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        (tmp_path / "v1" / "worker.exe").unlink()
        assert offline_pack.offline_pack_exists("v1", root=tmp_path) is False

    def test_malformed_manifest_returns_false(self, tmp_path: Path):
        root = tmp_path / "v1"
        root.mkdir()
        (root / "worker.exe").write_bytes(b"x")
        (root / "pack-manifest.json").write_text("{broken json")
        assert offline_pack.offline_pack_exists("v1", root=tmp_path) is False

    def test_completely_missing_dir_returns_false(self, tmp_path: Path):
        # No version directory at all.
        assert offline_pack.offline_pack_exists("nonexistent-version", root=tmp_path) is False


class TestBackgroundChecksum:
    """§8.10 / §8.16, background checksum on a daemon thread."""

    def test_valid_pack_publishes_pack_verified(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        events: list[dict] = []

        class FakeBus:
            def publish(self, ev):
                events.append(ev)

        bg = offline_pack.BackgroundChecksum("v1", event_bus=FakeBus(), root=tmp_path)
        bg.start()
        result = bg.join(timeout_s=5.0)
        assert result is True
        verified = [e for e in events if e["type"] == "offline_pack_verified"]
        assert verified
        assert verified[0]["data"]["version"] == "v1"

    def test_corrupt_pack_publishes_pack_corrupt(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        # Tamper with the worker.exe. SHA-256 will mismatch.
        (tmp_path / "v1" / "worker.exe").write_bytes(b"TAMPERED")
        events: list[dict] = []

        class FakeBus:
            def publish(self, ev):
                events.append(ev)

        bg = offline_pack.BackgroundChecksum("v1", event_bus=FakeBus(), root=tmp_path)
        bg.start()
        result = bg.join(timeout_s=5.0)
        assert result is False
        corrupt = [e for e in events if e["type"] == "offline_pack_corrupt"]
        assert corrupt
        assert corrupt[0]["data"]["version"] == "v1"

    def test_done_property_clears_after_run(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        bg = offline_pack.BackgroundChecksum("v1", root=tmp_path)
        assert bg.done is False
        bg.start()
        bg.join(timeout_s=5.0)
        assert bg.done is True

    def test_join_before_start_returns_none(self, tmp_path: Path):
        bg = offline_pack.BackgroundChecksum("v1", root=tmp_path)
        # No start() call → join returns None (no thread to join).
        assert bg.join(timeout_s=0.1) is None

    def test_start_idempotent(self, tmp_path: Path):
        """Calling ``start()`` twice does not spawn two threads."""
        _write_valid_pack(tmp_path, "v1")
        bg = offline_pack.BackgroundChecksum("v1", root=tmp_path)
        bg.start()
        first_thread = bg._thread
        bg.start()
        assert bg._thread is first_thread  # same thread
        bg.join(timeout_s=5.0)


class TestLaunchCheck:
    """``startup_tasks.check_offline_pack_on_launch``: always-on remote check."""

    def test_pack_present_starts_checksum_and_runs_update_check(self, monkeypatch):
        """Present pack → checksum AND remote update check on every launch."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: "v1")
        started: list[tuple] = []
        calls: list[dict] = []

        class FakeBackground:
            def __init__(self, version, *, event_bus=None):
                started.append((version, event_bus))

            def start(self):
                pass

        monkeypatch.setattr(offline_pack, "BackgroundChecksum", FakeBackground)

        def fake_check(config, event_bus, *, trigger_download=True, manifest_timeout=30.0):
            calls.append({"trigger_download": trigger_download, "timeout": manifest_timeout})
            return {"success": True, "update_available": False, "download_triggered": False}

        monkeypatch.setattr(update_check, "check_offline_pack_update", fake_check)
        result = startup_tasks.check_offline_pack_on_launch(SimpleNamespace(config=None))
        assert result["checked"] is True
        assert result["installed_version"] == "v1"
        assert result["checksum"] == "background"
        assert result["missing_event"] is False
        assert result["update_check"]["success"] is True
        assert started[0][0] == "v1"
        assert len(calls) == 1
        assert calls[0]["trigger_download"] is True
        assert calls[0]["timeout"] == update_check.LAUNCH_MANIFEST_TIMEOUT_S

    def test_pack_present_triggers_download_when_remote_newer(self, monkeypatch):
        """Always-on: present pack still downloads when remote is newer."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: "1.0.0")

        class FakeBackground:
            def __init__(self, version, *, event_bus=None):
                pass

            def start(self):
                pass

        monkeypatch.setattr(offline_pack, "BackgroundChecksum", FakeBackground)

        def fake_check(config, event_bus, *, trigger_download=True, manifest_timeout=30.0):
            assert trigger_download is True
            return {"success": True, "update_available": True, "download_triggered": True}

        monkeypatch.setattr(update_check, "check_offline_pack_update", fake_check)
        result = startup_tasks.check_offline_pack_on_launch(SimpleNamespace(config=None))
        assert result["update_check"]["download_triggered"] is True

    def test_pack_missing_publishes_event_and_triggers_download(self, monkeypatch):
        """Missing → offline_pack_missing + always-on re-download check."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: None)
        published: list[tuple] = []
        monkeypatch.setattr(
            offline_pack, "_publish_event", lambda bus, etype, payload: published.append((etype, payload))
        )
        calls: list[dict] = []

        def fake_check(config, event_bus, *, trigger_download=True, manifest_timeout=30.0):
            calls.append({"config": config, "trigger_download": trigger_download})
            return {"success": True, "download_triggered": True}

        monkeypatch.setattr(update_check, "check_offline_pack_update", fake_check)
        app = SimpleNamespace(config=SimpleNamespace(offline_pack_consent=True))
        result = startup_tasks.check_offline_pack_on_launch(app)
        assert result["checked"] is True
        assert result["installed_version"] is None
        assert result["checksum"] is None
        assert result["missing_event"] is True
        assert result["update_check"]["success"] is True
        assert published[0][0] == "offline_pack_missing"
        assert calls == [{"config": app.config, "trigger_download": True}]

    def test_always_on_legacy_consent_false_still_checks(self, monkeypatch):
        """Config consent flag False does not block launch update check."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: None)
        monkeypatch.setattr(offline_pack, "_publish_event", lambda bus, etype, payload: None)
        calls: list[bool] = []

        def fake_check(config, event_bus, *, trigger_download=True, manifest_timeout=30.0):
            calls.append(trigger_download)
            return {"success": True, "download_triggered": True}

        monkeypatch.setattr(update_check, "check_offline_pack_update", fake_check)
        result = startup_tasks.check_offline_pack_on_launch(SimpleNamespace(config=None))
        assert calls == [True]
        assert result["update_check"]["success"] is True
        assert result["update_check"].get("consent_required") is not True

    def test_shutdown_event_short_circuits_before_download(self, monkeypatch):
        """Shutdown requested → no checksum, no remote check."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: "v1")
        started: list = []
        checks: list = []

        class FakeBackground:
            def __init__(self, version, *, event_bus=None):
                started.append(version)

            def start(self):
                pass

        monkeypatch.setattr(offline_pack, "BackgroundChecksum", FakeBackground)

        def fake_check(*a, **k):
            checks.append(1)
            return {"success": True}

        monkeypatch.setattr(update_check, "check_offline_pack_update", fake_check)
        ev = threading.Event()
        ev.set()
        result = startup_tasks.check_offline_pack_on_launch(SimpleNamespace(config=None), ev)
        assert result == {"checked": False, "reason": "shutdown"}
        assert started == []
        assert checks == []

    def test_broken_pack_scan_degrades_gracefully(self, monkeypatch):
        """Broken scan → graceful failure result (update_check catches it), no raise."""

        def boom(root=None):
            raise RuntimeError("broken pack root")

        monkeypatch.setattr(update_check, "_local_offline_pack_version", boom)
        monkeypatch.setattr(offline_pack, "_publish_event", lambda bus, etype, payload: None)

        def fake_scan_failed(config, event_bus, *, trigger_download=True, manifest_timeout=30.0):
            return {"success": False, "error": "scan failed"}

        monkeypatch.setattr(
            update_check,
            "check_offline_pack_update",
            fake_scan_failed,
        )
        result = startup_tasks.check_offline_pack_on_launch(SimpleNamespace(config=None))
        assert result["checked"] is True
        assert result["installed_version"] is None
        assert result["update_check"]["success"] is False

    def test_launch_check_uses_short_manifest_timeout(self, monkeypatch):
        """Launch path bounds the remote fetch with ``LAUNCH_MANIFEST_TIMEOUT_S``."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: None)
        monkeypatch.setattr(offline_pack, "_publish_event", lambda bus, etype, payload: None)
        captured: dict = {}

        def fake_check(config, event_bus, *, trigger_download=True, manifest_timeout=30.0):
            captured["manifest_timeout"] = manifest_timeout
            return {"success": False, "reason": "fetch_failed"}

        monkeypatch.setattr(update_check, "check_offline_pack_update", fake_check)
        result = startup_tasks.check_offline_pack_on_launch(SimpleNamespace(config=None))
        assert captured.get("manifest_timeout") == update_check.LAUNCH_MANIFEST_TIMEOUT_S
        assert result["checked"] is True

    def test_outer_guard_never_raises(self, monkeypatch):
        """Unexpected error in the update check → best-effort dict, no raise."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: None)
        monkeypatch.setattr(offline_pack, "_publish_event", lambda bus, etype, payload: None)

        def boom(config, event_bus, *, trigger_download=True, manifest_timeout=30.0):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(update_check, "check_offline_pack_update", boom)
        result = startup_tasks.check_offline_pack_on_launch(SimpleNamespace(config=None))
        assert result["checked"] is True
        assert result["installed_version"] is None
        assert result["update_check"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
