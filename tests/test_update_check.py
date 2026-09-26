"""§10: tests for the pack-version checker (``update_check.py``)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from voice_typer.server.service import update_check
from voice_typer.server.service.update_check import (
    DEFAULT_OFFLINE_PACK_MANIFEST_URL,
    MAX_MANIFEST_BYTES,
    check_offline_pack_update,
    fetch_remote_manifest,
    handle_check_offline_pack_update_ipc,
    is_newer_version,
)


@pytest.fixture(autouse=True)
def _hermetic_trigger_disk_gate(monkeypatch):
    """Make the tests in this file disk-independent."""
    monkeypatch.setattr(
        "voice_typer.server.service.offline_pack.check_offline_pack_disk_space",
        lambda *args, **kwargs: None,
    )


def _make_manifest(version: str = "1.2.3", *, sha256: str | None = None) -> dict:
    """Build a minimal valid pack-manifest.json dict."""
    if sha256 is None:
        sha256 = hashlib.sha256(b"pack-body").hexdigest()
    return {
        "version": version,
        "sha256": sha256,
        "files": [
            {"name": "worker.exe", "sha256": hashlib.sha256(b"worker").hexdigest(), "size": 1024},
        ],
        "min_proto_version": 1,
    }


@pytest.fixture
def fake_manifest_url() -> str:
    """A fake manifest URL on the GitHub Releases host (SSRF-allowed)."""
    return "https://github.com/AbdallahIsDev/voice-typer/releases/latest/download/pack-manifest.json"


@pytest.fixture
def fake_event_bus():
    """A fake event bus that captures published events for assertions."""
    events: list[dict] = []

    class _FakeBus:
        def publish(self, event: dict) -> None:
            events.append(event)

    bus = _FakeBus()
    return SimpleNamespace(bus=bus, events=events)


@pytest.fixture
def fake_config_with_consent():
    """A fake config object with ``offline_pack_consent=True``."""
    return SimpleNamespace(offline_pack_consent=True)


@pytest.fixture
def fake_config_no_consent():
    """A fake config object with ``offline_pack_consent=False``."""
    return SimpleNamespace(offline_pack_consent=False)


class TestIsNewerVersion:
    """``is_newer_version``, semver-ish comparison."""

    @pytest.mark.parametrize(
        "remote,local,expected",
        [
            ("1.2.3", "1.2.2", True),
            ("1.2.3", "1.2.3", False),
            ("1.3.0", "1.2.3", True),
            ("2.0.0", "1.9.9", True),
            ("v2.0.0", "1.9.9", True),  # leading 'v' stripped
            ("1.2", "1.2.0", False),  # shorter tuple pads with zeros
            ("1.2.0", "1.2", False),
            ("1.2.3", "1.2.3-rc1", False),  # suffix ignored
            ("1.2.3-rc1", "1.2.3", False),  # equal after suffix strip
            ("1.2.3", "2.0.0", False),  # remote older
            ("", "", False),  # empty strings
            ("1.2.3", "", True),  # local empty → remote is "newer"
        ],
    )
    def test_comparison(self, remote: str, local: str, expected: bool):
        assert is_newer_version(remote, local) is expected, (
            f"is_newer_version({remote!r}, {local!r}) should be {expected}"
        )

    def test_non_numeric_segments_treated_as_zero(self):
        """Non-numeric segments (e.g. ``\"1.2.x\"``) are treated as 0."""
        assert is_newer_version("1.2.x", "1.2.0") is False
        assert is_newer_version("1.2.0", "1.2.x") is False


class TestFetchRemoteManifest:
    """``fetch_remote_manifest``, SSRF + max-bytes + schema validation."""

    def test_fetches_and_parses_valid_manifest(self, fake_manifest_url: str):
        """A valid manifest is fetched + parsed + structurally validated."""
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest).encode("utf-8")

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            assert url == fake_manifest_url
            return body.decode("utf-8")

        result = fetch_remote_manifest(fake_manifest_url, http_get=fake_http_get)
        assert result is not None
        assert result["version"] == "1.2.3"
        assert result["sha256"] == manifest["sha256"]

    def test_returns_none_on_ssrf_block(self):
        """A private-IP URL is rejected by ``assert_pack_url_allowed``."""

        # ``assert_pack_url_allowed`` extends the allowlist with GitHub
        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            raise AssertionError("HTTP transport should NOT be called for SSRF-blocked URL")

        result = fetch_remote_manifest("https://10.0.0.5/pack-manifest.json", http_get=fake_http_get)
        assert result is None

    def test_returns_none_on_non_allowlisted_host(self):
        """A non-allowlisted host (not GitHub + not in the allowlist) is rejected."""

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            raise AssertionError("HTTP transport should NOT be called for non-allowlisted host")

        result = fetch_remote_manifest("https://evil.example.com/pack-manifest.json", http_get=fake_http_get)
        assert result is None

    def test_returns_none_on_network_error(self, fake_manifest_url: str):
        """An OSError during fetch → None (no exception propagates)."""

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            raise OSError("simulated DNS failure")

        result = fetch_remote_manifest(fake_manifest_url, http_get=fake_http_get)
        assert result is None

    def test_http_404_is_info_not_warning(self, fake_manifest_url: str, caplog):
        """A 404 (no pack release published yet) is the expected"""

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            raise RuntimeError(f"unexpected HTTP status 404 for {url}")

        with caplog.at_level("INFO", logger="voice_typer.server.service.update_check"):
            assert fetch_remote_manifest(fake_manifest_url, http_get=fake_http_get) is None
        assert not any(r.levelname == "WARNING" and "[UPDATE]" in r.message for r in caplog.records), (
            f"404 must not warn; got: {[(r.levelname, r.message) for r in caplog.records]!r}"
        )
        assert any("[UPDATE]" in r.message and r.levelname == "INFO" for r in caplog.records), (
            f"expected INFO-level UPDATE record; got: {[(r.levelname, r.message) for r in caplog.records]!r}"
        )
        assert "Traceback" not in caplog.text

    def test_http_error_404_object_is_info(self, fake_manifest_url: str, caplog):
        """The real ``urllib.error.HTTPError`` (``.code == 404``) also maps to INFO."""
        import urllib.error

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

        with caplog.at_level("INFO", logger="voice_typer.server.service.update_check"):
            assert fetch_remote_manifest(fake_manifest_url, http_get=fake_http_get) is None
        assert not any(r.levelname == "WARNING" and "[UPDATE]" in r.message for r in caplog.records)

    def test_non_404_network_error_stays_warning(self, fake_manifest_url: str, caplog):
        """Genuine outages (DNS/timeout) keep WARNING so they stay visible."""

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            raise OSError("simulated DNS failure")

        with caplog.at_level("INFO", logger="voice_typer.server.service.update_check"):
            assert fetch_remote_manifest(fake_manifest_url, http_get=fake_http_get) is None
        assert any(r.levelname == "WARNING" and "[UPDATE]" in r.message for r in caplog.records), (
            f"non-404 failure must warn; got: {[(r.levelname, r.message) for r in caplog.records]!r}"
        )

    def test_returns_none_on_invalid_json(self, fake_manifest_url: str):
        """A non-JSON response → None."""

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return "not json {{{"

        result = fetch_remote_manifest(fake_manifest_url, http_get=fake_http_get)
        assert result is None

    def test_returns_none_on_schema_validation_failure(self, fake_manifest_url: str):
        """A JSON response that fails ``load_offline_pack_manifest`` schema validation → None."""
        # Missing required 'version' field.
        bad_manifest = {"sha256": "0" * 64, "files": [], "min_proto_version": 1}

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return json.dumps(bad_manifest)

        result = fetch_remote_manifest(fake_manifest_url, http_get=fake_http_get)
        assert result is None

    def test_oversized_manifest_rejected(self, fake_manifest_url: str):
        """A manifest body exceeding ``max_bytes`` is rejected."""
        # Build a body that's just over the 1 MiB cap.
        big_body = "x" * (MAX_MANIFEST_BYTES + 1)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            # The transport itself enforces the cap (defense-in-depth
            if len(big_body) > max_bytes:
                raise RuntimeError(f"manifest exceeds max_bytes={max_bytes} (read {len(big_body)} bytes so far)")
            return big_body

        result = fetch_remote_manifest(fake_manifest_url, http_get=fake_http_get)
        assert result is None

    def test_proxy_env_passed_through(self, fake_manifest_url: str, monkeypatch):
        """The default transport reads ``HTTP_PROXY`` / ``HTTPS_PROXY`` env vars."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example.com:8080")
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp.example.com:8080")
        from voice_typer.server.service.offline_pack import proxy_env

        env = proxy_env()
        assert env.get("HTTPS_PROXY") == "http://proxy.corp.example.com:8080"
        assert env.get("HTTP_PROXY") == "http://proxy.corp.example.com:8080"

    def test_lowercase_proxy_env_vars_supported(self, monkeypatch):
        """``requests`` / ``httpx`` honor lowercase proxy env vars; so does ``proxy_env``."""
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.setenv("https_proxy", "http://proxy.corp.example.com:8080")
        monkeypatch.setenv("http_proxy", "http://proxy.corp.example.com:8080")
        from voice_typer.server.service.offline_pack import proxy_env

        env = proxy_env()
        assert env.get("https_proxy") == "http://proxy.corp.example.com:8080"
        assert env.get("http_proxy") == "http://proxy.corp.example.com:8080"


class TestCheckOfflinePackUpdate:
    """``check_offline_pack_update``, the main entry point."""

    def test_no_local_pack_remote_available_triggers_download(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """When no local pack exists + remote is available + consent given →"""
        # No local pack → local_version=None → update_available=True.
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: None)
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        # Mock the background download trigger so we don't actually
        download_called = {"called": False}

        def fake_trigger(
            *,
            manifest,
            manifest_url,
            config,
            event_bus,
            root,
            http_get,
        ):
            download_called["called"] = True
            return True

        monkeypatch.setattr(update_check, "_trigger_background_download", fake_trigger)

        result = check_offline_pack_update(
            fake_config_with_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            manifest_url=fake_manifest_url,
        )

        assert result["success"] is True
        assert result["update_available"] is True
        assert result["local_version"] is None
        assert result["remote_version"] == "1.2.3"
        assert result["download_triggered"] is True
        assert download_called["called"] is True

    def test_up_to_date_pack_no_download(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """When local == remote → no download triggered."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: "1.2.3")
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        download_called = {"called": False}

        def fake_trigger(**kwargs):
            download_called["called"] = True
            return True

        monkeypatch.setattr(update_check, "_trigger_background_download", fake_trigger)

        result = check_offline_pack_update(
            fake_config_with_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            manifest_url=fake_manifest_url,
        )

        assert result["success"] is True
        assert result["update_available"] is False
        assert result["local_version"] == "1.2.3"
        assert result["remote_version"] == "1.2.3"
        assert result["download_triggered"] is False
        assert download_called["called"] is False

    def test_newer_remote_triggers_download(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """Local 1.2.2, remote 1.2.3 → update_available + download_triggered."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: "1.2.2")
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        download_called = {"called": False}

        def fake_trigger(**kwargs):
            download_called["called"] = True
            return True

        monkeypatch.setattr(update_check, "_trigger_background_download", fake_trigger)

        result = check_offline_pack_update(
            fake_config_with_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            manifest_url=fake_manifest_url,
        )

        assert result["success"] is True
        assert result["update_available"] is True
        assert result["local_version"] == "1.2.2"
        assert result["remote_version"] == "1.2.3"
        assert result["download_triggered"] is True

    def test_consent_false_still_checks_and_downloads(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_no_consent,
        monkeypatch,
    ):
        """Always-on: consent flag False does NOT block fetch or download."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: None)
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)
        http_called: list[str] = []
        triggered: list[bool] = []

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            http_called.append(url)
            return body

        monkeypatch.setattr(
            update_check,
            "_trigger_background_download",
            lambda **kwargs: triggered.append(True) or True,
        )

        result = check_offline_pack_update(
            fake_config_no_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            manifest_url=fake_manifest_url,
        )

        assert http_called != [], "always-on must fetch the remote manifest"
        assert result["success"] is True
        assert result.get("consent_required") is not True
        assert result["update_available"] is True
        assert result["download_triggered"] is True
        assert triggered == [True]
        consent_events = [e for e in fake_event_bus.events if e["type"] == "consent_required"]
        assert consent_events == []

    def test_consent_off_with_local_pack_still_checks_remote(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_no_consent,
        monkeypatch,
    ):
        """Local pack installed → remote check still runs regardless of consent flag."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: "1.2.3")
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)
        http_called: list[str] = []

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            http_called.append(url)
            return body

        result = check_offline_pack_update(
            fake_config_no_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            manifest_url=fake_manifest_url,
        )

        assert http_called != []
        assert result["success"] is True
        assert result["update_available"] is False
        assert result.get("consent_required") is not True

    def test_manifest_timeout_reaches_default_transport(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """``manifest_timeout`` is plumbed to the default HTTP transport."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: None)
        captured: dict[str, float] = {}

        def fake_default_transport(url, *, max_bytes=MAX_MANIFEST_BYTES, timeout: float = 30.0):
            captured["timeout"] = timeout
            return json.dumps(_make_manifest("9.9.9"))

        monkeypatch.setattr(update_check, "_http_get_manifest", fake_default_transport)
        monkeypatch.setattr(update_check, "_trigger_background_download", lambda **kwargs: True)

        result = check_offline_pack_update(
            fake_config_with_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            manifest_url=fake_manifest_url,
            manifest_timeout=7.5,
        )

        assert captured.get("timeout") == 7.5
        assert result["remote_version"] == "9.9.9"

    def test_launch_timeout_constant_is_short(self):
        """The launch-time fetch budget stays well under the interactive default."""
        assert update_check.LAUNCH_MANIFEST_TIMEOUT_S <= 10.0
        assert update_check.LAUNCH_MANIFEST_TIMEOUT_S > 0

    def test_fetch_failure_returns_error(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """When the remote manifest can't be fetched → ``{success: False, reason: 'fetch_failed'}``."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: "1.2.3")

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            raise OSError("simulated network failure")

        result = check_offline_pack_update(
            fake_config_with_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            manifest_url=fake_manifest_url,
        )

        assert result["success"] is False
        assert result["reason"] == "fetch_failed"
        assert result["update_available"] is False
        assert result["download_triggered"] is False
        assert "error" in result

    def test_missing_manifest_404_returns_fetch_failed_without_download(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """A 404 with consent given → ``fetch_failed``, no download, no"""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: None)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            raise RuntimeError(f"unexpected HTTP status 404 for {url}")

        def fail_on_trigger(**kwargs):
            raise AssertionError("no download may trigger when the manifest is absent")

        monkeypatch.setattr(update_check, "_trigger_background_download", fail_on_trigger)

        result = check_offline_pack_update(
            fake_config_with_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            manifest_url=fake_manifest_url,
        )

        assert result["success"] is False
        assert result["reason"] == "fetch_failed"
        assert result["download_triggered"] is False
        assert result.get("consent_required", False) is False

    def test_trigger_download_false_skips_download(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """``trigger_download=False`` → check runs but download is NOT triggered."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: None)
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        download_called = {"called": False}

        def fake_trigger(**kwargs):
            download_called["called"] = True
            return True

        monkeypatch.setattr(update_check, "_trigger_background_download", fake_trigger)

        result = check_offline_pack_update(
            fake_config_with_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            manifest_url=fake_manifest_url,
            trigger_download=False,
        )

        assert result["success"] is True
        assert result["update_available"] is True
        assert result["download_triggered"] is False
        assert download_called["called"] is False

    def test_env_var_override_for_manifest_url(
        self,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """``VT_PACK_MANIFEST_URL`` env var overrides the default URL."""
        custom_url = "https://github.com/my-org/my-fork/releases/latest/download/pack-manifest.json"
        monkeypatch.setenv("VT_PACK_MANIFEST_URL", custom_url)
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: None)

        fetched_urls: list[str] = []
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            fetched_urls.append(url)
            return body

        def fake_trigger(**kwargs):
            return True

        monkeypatch.setattr(update_check, "_trigger_background_download", fake_trigger)

        check_offline_pack_update(
            fake_config_with_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            # NOTE: manifest_url NOT passed, should fall back to env var.
        )

        assert fetched_urls == [custom_url], f"expected fetch from env-var URL {custom_url!r}, got {fetched_urls}"

    def test_default_manifest_url_is_github_releases_latest(self):
        """The default manifest URL points at GitHub Releases ``/latest/download/``."""
        from voice_typer.server.branding import APP_REPO

        assert DEFAULT_OFFLINE_PACK_MANIFEST_URL == (
            "https://github.com/AbdallahIsDev/lausu/releases/latest/download/pack-manifest.json"
        ), (
            "DEFAULT_OFFLINE_PACK_MANIFEST_URL changed, update docs/auto-update-feature.md "
            "(Sub-agent 15) and the publisher (publish_pack_release.py) to match."
        )
        assert (
            f"https://github.com/{APP_REPO}/releases/latest/download/pack-manifest.json"
        ) == DEFAULT_OFFLINE_PACK_MANIFEST_URL, (
            "manifest URL must be derived from branding.APP_REPO, not a re-hardcoded owner/name pair"
        )

    def test_result_includes_checked_at_epoch_ms(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """The result includes ``checked_at`` (epoch ms) for UI display."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: "1.2.3")
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        before = int(time.time() * 1000)
        result = check_offline_pack_update(
            fake_config_with_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            manifest_url=fake_manifest_url,
        )
        after = int(time.time() * 1000)

        assert "checked_at" in result
        assert before <= result["checked_at"] <= after, f"checked_at {result['checked_at']} not in [{before}, {after}]"


class TestTriggerBackgroundDownload:
    """``_trigger_background_download``, spawns the download thread."""

    def test_spawns_thread_with_correct_url(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """The download URL is constructed from the manifest URL + version."""
        manifest = _make_manifest("1.2.3")
        captured: dict = {}

        def fake_download(url, dest, *, expected_sha256, version, event_bus, http_get=None):
            captured["url"] = url
            captured["dest"] = dest
            captured["expected_sha256"] = expected_sha256
            captured["version"] = version
            return True

        # Patch ``offline_pack.download_offline_pack_with_resume`` at the pack module
        monkeypatch.setattr(
            "voice_typer.server.service.offline_pack.download_offline_pack_with_resume",
            fake_download,
        )

        ok = update_check._trigger_background_download(
            manifest=manifest,
            manifest_url=fake_manifest_url,
            config=fake_config_with_consent,
            event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
            root=None,
            http_get=None,
        )
        assert ok is True

        deadline = time.monotonic() + 2.0
        while not captured and time.monotonic() < deadline:
            time.sleep(0.01)
        # Yield to let the daemon thread run.
        for _ in range(10):
            if captured:
                break
            time.sleep(0.01)

        assert "url" in captured, "download was not called within 2s"
        assert captured["url"] == (
            "https://github.com/AbdallahIsDev/voice-typer/releases/latest/download/pack-1.2.3.zip"
        ), f"download URL should be constructed from manifest URL + version, got {captured['url']!r}"
        assert captured["expected_sha256"] == manifest["sha256"]
        assert captured["version"] == "1.2.3"

    def test_trigger_allows_without_consent(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_no_consent,
        monkeypatch,
    ):
        """Always-on: missing consent does not raise; download path is opened."""
        monkeypatch.setattr(
            update_check.offline_pack,
            "download_offline_pack_with_resume",
            lambda *a, **k: False,
        )
        monkeypatch.setattr(
            update_check.offline_pack,
            "check_offline_pack_disk_space",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            update_check.offline_pack,
            "install_offline_pack",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            update_check.offline_pack,
            "offline_pack_exists",
            lambda *a, **k: False,
        )
        monkeypatch.setattr(
            update_check.offline_pack,
            "OfflinePackLock",
            lambda *a, **k: SimpleNamespace(__enter__=lambda s: s, __exit__=lambda *a: False),
        )
        monkeypatch.setattr(
            update_check.offline_pack,
            "offline_pack_partial_path",
            lambda version, root=None: Path(f"tmp_pack_{version}") / "partial.zip",
        )
        manifest = _make_manifest("1.2.3")
        ok = update_check._trigger_background_download(
            manifest=manifest,
            manifest_url=fake_manifest_url,
            config=fake_config_no_consent,
            event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
            root=None,
            http_get=None,
        )
        assert ok is True

    def test_second_concurrent_trigger_is_skipped(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """§8.13/§8.16, two triggers for the same version → ONE download."""
        manifest = _make_manifest("2.0.0")
        entered = threading.Event()
        release = threading.Event()

        def fake_download(url, dest, *, expected_sha256, version, event_bus, http_get=None):
            entered.set()
            release.wait(5.0)  # hold the guard until the test checks it
            return True

        monkeypatch.setattr(
            "voice_typer.server.service.offline_pack.download_offline_pack_with_resume",
            fake_download,
        )

        first = update_check._trigger_background_download(
            manifest=manifest,
            manifest_url=fake_manifest_url,
            config=fake_config_with_consent,
            event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
            root=None,
            http_get=None,
        )
        assert first is True
        assert entered.wait(2.0), "first download thread never started"

        # Second trigger while the first is still in flight → skipped.
        second = update_check._trigger_background_download(
            manifest=manifest,
            manifest_url=fake_manifest_url,
            config=fake_config_with_consent,
            event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
            root=None,
            http_get=None,
        )
        assert second is False
        release.set()

    def test_guard_releases_after_download_finishes(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """Once the in-flight download completes, a later trigger retries."""
        manifest = _make_manifest("2.1.0")
        started: list[int] = []
        done = threading.Event()

        def fake_download(url, dest, *, expected_sha256, version, event_bus, http_get=None):
            started.append(1)
            done.set()
            return True

        monkeypatch.setattr(
            "voice_typer.server.service.offline_pack.download_offline_pack_with_resume",
            fake_download,
        )

        assert (
            update_check._trigger_background_download(
                manifest=manifest,
                manifest_url=fake_manifest_url,
                config=fake_config_with_consent,
                event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
                root=None,
                http_get=None,
            )
            is True
        )
        assert done.wait(2.0), "download thread never ran"
        # Give the finally-block time to release the guard.
        deadline = time.monotonic() + 2.0
        while update_check._ACTIVE_PACK_DOWNLOADS and time.monotonic() < deadline:
            time.sleep(0.01)

        assert (
            update_check._trigger_background_download(
                manifest=manifest,
                manifest_url=fake_manifest_url,
                config=fake_config_with_consent,
                event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
                root=None,
                http_get=None,
            )
            is True
        )


class TestHandleCheckPackUpdateIpc:
    """``handle_check_offline_pack_update_ipc``, thin IPC wrapper."""

    def test_returns_plain_dict(self, fake_config_with_consent, fake_event_bus, monkeypatch):
        """The IPC handler returns a plain ``dict`` (not a TypedDict instance)."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: "1.2.3")
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        app = SimpleNamespace(
            config=fake_config_with_consent,
            event_bus=fake_event_bus.bus,
        )
        result = handle_check_offline_pack_update_ipc(app, None, http_get=fake_http_get)

        assert isinstance(result, dict)
        assert "success" in result
        assert result["success"] is True

    def test_app_none_tolerated(self, monkeypatch):
        """``app=None`` is tolerated, treated as no-config + no-event-bus."""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: None)
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        result = handle_check_offline_pack_update_ipc(None, None, http_get=fake_http_get)
        assert isinstance(result, dict)
        # Always-on: no consent_required path; fetch failed (no local pack path mocked well).
        assert result.get("consent_required") is not True

    def test_app_without_event_bus_attribute_falls_back_to_module(
        self,
        fake_config_with_consent,
        monkeypatch,
    ):
        """When ``app.event_bus`` is missing, the handler falls back to the"""
        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda root=None: "1.2.3")
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        app = SimpleNamespace(config=fake_config_with_consent)
        result = handle_check_offline_pack_update_ipc(app, None, http_get=fake_http_get)
        assert isinstance(result, dict)
        assert result["success"] is True


class TestMaxBytesCapInherited:
    """The max-bytes cap is inherited from ``_secure_read_text``."""

    def test_cap_is_one_mebibyte(self):
        """``MAX_MANIFEST_BYTES`` is 1 MiB (1048576 bytes)."""
        assert MAX_MANIFEST_BYTES == 1024 * 1024

    def test_secure_read_text_rejects_oversized(self, tmp_path: Path):
        """
        ``_secure_read_text`` rejects a file exceeding ``max_bytes``.
        This is a re-test of the contract from
        """
        from voice_typer.server.secure_file_io import _secure_read_text

        f = tmp_path / "big.json"
        f.write_text("x" * (MAX_MANIFEST_BYTES + 1))
        with pytest.raises(ValueError, match="max_bytes"):
            _secure_read_text(f, max_bytes=MAX_MANIFEST_BYTES)

    def test_secure_read_text_accepts_at_boundary(self, tmp_path: Path):
        """A file exactly at ``max_bytes`` succeeds (the cap is exclusive)."""
        from voice_typer.server.secure_file_io import _secure_read_text

        f = tmp_path / "boundary.json"
        f.write_text("x" * MAX_MANIFEST_BYTES)
        # Should NOT raise.
        content = _secure_read_text(f, max_bytes=MAX_MANIFEST_BYTES)
        assert len(content) == MAX_MANIFEST_BYTES


class TestSSRFInherited:
    """The SSRF defense is inherited from ``assert_pack_url_allowed``."""

    def test_github_hosts_in_allowlist_after_call(self, fake_manifest_url: str):
        """After calling ``fetch_remote_manifest`` (which calls"""
        from voice_typer.server.security.url_allowlist import get_url_allowlist

        with contextlib.suppress(Exception):
            fetch_remote_manifest(
                fake_manifest_url,
                http_get=lambda url, **kw: json.dumps(_make_manifest()).decode("utf-8"),
            )

        allowlist = get_url_allowlist()
        assert "github.com" in allowlist
        assert "objects.githubusercontent.com" in allowlist
        assert "codeload.github.com" in allowlist

    def test_private_ip_literal_rejected_even_if_allowlisted(self):
        """Even if a private IP is added to the allowlist, the SSRF check"""
        from voice_typer.server.security.url_allowlist import (
            _user_extensions,
            assert_url_allowed,
            extend_url_allowlist,
        )

        try:
            extend_url_allowlist(["10.0.0.5"], caller="test")
            with pytest.raises(ValueError, match="private/reserved IP literal"):
                assert_url_allowed("https://10.0.0.5/path", check_dns_rebinding=False)
        finally:
            _user_extensions.discard("10.0.0.5")


class TestSSRFRedirectRevalidation:
    """``_SSRFAwareRedirectHandler`` re-validates each 3xx redirect target."""

    def test_redirect_handler_rejects_private_ip_target(self):
        """``_SSRFAwareRedirectHandler.redirect_request`` raises"""
        from urllib.request import Request

        from voice_typer.server.service.update_check import _SSRFAwareRedirectHandler

        handler = _SSRFAwareRedirectHandler()
        req = Request("https://github.com/owner/repo/pack-manifest.json")

        with pytest.raises(RuntimeError, match="SSRF"):
            handler.redirect_request(
                req,
                fp=None,
                code=302,
                msg="Found",
                headers=None,
                newurl="http://10.0.0.5/evil",
            )

    def test_redirect_handler_rejects_loopback_http_target(self):
        """A ``http://127.0.0.1/evil`` redirect target is rejected (HTTP"""
        from urllib.request import Request

        from voice_typer.server.service.update_check import _SSRFAwareRedirectHandler

        handler = _SSRFAwareRedirectHandler()
        req = Request("https://github.com/owner/repo/pack-manifest.json")

        with pytest.raises(RuntimeError, match="SSRF"):
            handler.redirect_request(
                req,
                fp=None,
                code=302,
                msg="Found",
                headers=None,
                newurl="http://127.0.0.1/evil",
            )

    def test_redirect_handler_accepts_allowlisted_target(self):
        """
        ``super().redirect_request()`` when the target is allowlisted
        This pins the positive path: a legitimate GitHub Releases
        """
        from urllib.request import Request

        from voice_typer.server.service.update_check import _SSRFAwareRedirectHandler

        handler = _SSRFAwareRedirectHandler()
        req = Request("https://github.com/owner/repo/releases/latest/download/pack-manifest.json")

        # ``objects.githubusercontent.com`` is in the pack allowlist
        result = handler.redirect_request(
            req,
            fp=None,
            code=302,
            msg="Found",
            headers=None,
            newurl="https://objects.githubusercontent.com/github-production-release-asset/foo",
        )
        assert result is not None, (
            "expected redirect to be followed for an allowlisted target, got redirect_request()=None (no follow)"
        )
        assert result.get_full_url() == "https://objects.githubusercontent.com/github-production-release-asset/foo"

    def test_manifest_redirect_to_private_ip_is_rejected(self, monkeypatch):
        """redirect is NOT followed."""
        import email.message
        import urllib.error
        import urllib.request

        from voice_typer.server.service import update_check

        redirect_target = "http://10.0.0.5/evil"

        # Build a fake HTTPS response that simulates a 302 redirect.
        class _FakeRedirectResponse:
            """A fake ``http.client.HTTPResponse`` that returns 302."""

            def __init__(self, location: str) -> None:
                self._location = location
                self.status = 302
                self.code = 302
                self.msg = "Found"
                self._headers = email.message.Message()
                self._headers["Location"] = location

            def getcode(self) -> int:
                return 302

            def info(self):
                return self._headers

            def read(self, size: int = -1) -> bytes:  # noqa: ARG002
                return b""

            def close(self) -> None:
                pass

            def __enter__(self) -> _FakeRedirectResponse:
                return self

            def __exit__(self, *args: object) -> None:
                pass

        # Fake HTTPS handler that always returns a 302 redirect response
        class _FakeHTTPSHandler(urllib.request.HTTPSHandler):
            def https_open(self, req):  # noqa: ARG002
                return _FakeRedirectResponse(redirect_target)

        # Fake HTTP handler that raises URLError. Installed alongside
        class _FakeHTTPHandler(urllib.request.HTTPHandler):
            def http_open(self, req):  # noqa: ARG002
                raise urllib.error.URLError(
                    f"test: refusing to follow redirect to HTTP target {redirect_target!r} (no real network in tests)"
                )

        # Patch ``build_opener`` so the opener installs our fake HTTPS
        real_build_opener = urllib.request.build_opener

        def fake_build_opener(*handlers):
            return real_build_opener(_FakeHTTPSHandler(), _FakeHTTPHandler(), *handlers)

        monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)

        initial_url = "https://github.com/owner/repo/pack-manifest.json"

        # ``_http_get_manifest`` should raise RuntimeError (SSRF block
        with pytest.raises(RuntimeError, match="SSRF") as exc_info:
            update_check._http_get_manifest(initial_url)

        assert "10.0.0.5" in str(exc_info.value) or "redirect" in str(exc_info.value).lower()

    def test_fetch_remote_manifest_returns_none_on_redirect_to_private_ip(self, monkeypatch):
        """``fetch_remote_manifest`` returns ``None`` when the manifest URL"""
        import email.message
        import urllib.error
        import urllib.request

        from voice_typer.server.service import update_check

        redirect_target = "http://10.0.0.5/evil"

        class _FakeRedirectResponse:
            def __init__(self, location: str) -> None:
                self._location = location
                self.status = 302
                self.code = 302
                self.msg = "Found"
                self._headers = email.message.Message()
                self._headers["Location"] = location

            def getcode(self) -> int:
                return 302

            def info(self):
                return self._headers

            def read(self, size: int = -1) -> bytes:  # noqa: ARG002
                return b""

            def close(self) -> None:
                pass

            def __enter__(self) -> _FakeRedirectResponse:
                return self

            def __exit__(self, *args: object) -> None:
                pass

        class _FakeHTTPSHandler(urllib.request.HTTPSHandler):
            def https_open(self, req):  # noqa: ARG002
                return _FakeRedirectResponse(redirect_target)

        class _FakeHTTPHandler(urllib.request.HTTPHandler):
            def http_open(self, req):  # noqa: ARG002
                raise urllib.error.URLError("test: refusing to follow redirect (no real network)")

        real_build_opener = urllib.request.build_opener

        def fake_build_opener(*handlers):
            return real_build_opener(_FakeHTTPSHandler(), _FakeHTTPHandler(), *handlers)

        monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)

        # Use the default ``_http_get_manifest`` (do NOT inject a fake
        initial_url = "https://github.com/owner/repo/pack-manifest.json"

        result = update_check.fetch_remote_manifest(initial_url)
        assert result is None, (
            "fetch_remote_manifest should return None (fail-closed) when "
            "the manifest URL redirects to a private IP, got "
            f"{result!r}"
        )


class TestTriggerInstallWiring:
    """disk gate, holds the cross-process pack lock around the download +"""

    def test_disk_gate_runs_before_download(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
        tmp_path: Path,
    ):
        """The §8.8 disk-space gate is invoked on the pack dir before the"""
        manifest = _make_manifest("1.1.0")
        gate_calls: list[Path] = []
        download_called = threading.Event()

        def fake_gate(pack_dir, **kwargs):
            gate_calls.append(Path(pack_dir))
            raise RuntimeError("insufficient disk space")

        def fake_download(url, dest, *, expected_sha256, version, event_bus, http_get=None):
            download_called.set()
            return True

        monkeypatch.setattr("voice_typer.server.service.offline_pack.check_offline_pack_disk_space", fake_gate)
        monkeypatch.setattr(
            "voice_typer.server.service.offline_pack.download_offline_pack_with_resume",
            fake_download,
        )

        assert (
            update_check._trigger_background_download(
                manifest=manifest,
                manifest_url=fake_manifest_url,
                config=fake_config_with_consent,
                event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
                root=tmp_path,
                http_get=None,
            )
            is True
        )
        # The background thread runs the gate (and never the download).
        deadline = time.monotonic() + 2.0
        while not gate_calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert gate_calls, "disk gate never ran within 2s"
        dest = update_check.offline_pack.offline_pack_partial_path("1.1.0", root=tmp_path)
        assert gate_calls[0] == dest.parent
        assert not download_called.wait(0.5), "download must not run when the disk gate fails"
        # Guard released despite the gate failure.
        deadline = time.monotonic() + 2.0
        while "1.1.0" in update_check._ACTIVE_PACK_DOWNLOADS and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "1.1.0" not in update_check._ACTIVE_PACK_DOWNLOADS

    def test_lock_wraps_download_and_install(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
        tmp_path: Path,
    ):
        """The §8.13 cross-process lock is held around the download AND"""
        manifest = _make_manifest("1.2.0")
        events_order: list[str] = []

        class _FakeLock:
            def __init__(self, version, *, root=None, timeout_s=None):
                pass

            def __enter__(self):
                events_order.append("lock_enter")
                return self

            def __exit__(self, *args):
                events_order.append("lock_exit")
                return None

        def fake_download(url, dest, *, expected_sha256, version, event_bus, http_get=None):
            events_order.append("download")
            return True

        def fake_install(archive, version, manifest, **kwargs):
            events_order.append("install")
            return True

        monkeypatch.setattr("voice_typer.server.service.offline_pack.OfflinePackLock", _FakeLock)
        monkeypatch.setattr(
            "voice_typer.server.service.offline_pack.download_offline_pack_with_resume",
            fake_download,
        )
        monkeypatch.setattr("voice_typer.server.service.offline_pack.install_offline_pack", fake_install)

        assert (
            update_check._trigger_background_download(
                manifest=manifest,
                manifest_url=fake_manifest_url,
                config=fake_config_with_consent,
                event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
                root=tmp_path,
                http_get=None,
            )
            is True
        )
        deadline = time.monotonic() + 2.0
        while "install" not in events_order and time.monotonic() < deadline:
            time.sleep(0.01)
        deadline = time.monotonic() + 2.0
        while "lock_exit" not in events_order and time.monotonic() < deadline:
            time.sleep(0.01)

        assert events_order[:2] == ["lock_enter", "download"]
        assert events_order.index("install") < events_order.index("lock_exit")

    def test_install_skipped_when_download_fails(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
        tmp_path: Path,
    ):
        """A failed download (SHA mismatch → False) must not install."""
        manifest = _make_manifest("1.3.0")
        install_called = threading.Event()

        def fake_download(url, dest, *, expected_sha256, version, event_bus, http_get=None):
            return False

        def fake_install(archive, version, manifest, **kwargs):
            install_called.set()
            return False

        monkeypatch.setattr(
            "voice_typer.server.service.offline_pack.download_offline_pack_with_resume",
            fake_download,
        )
        monkeypatch.setattr("voice_typer.server.service.offline_pack.install_offline_pack", fake_install)

        assert (
            update_check._trigger_background_download(
                manifest=manifest,
                manifest_url=fake_manifest_url,
                config=fake_config_with_consent,
                event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
                root=tmp_path,
                http_get=None,
            )
            is True
        )
        assert not install_called.wait(1.0), "install must not run when the download fails"

    def test_bg_skips_download_when_pack_already_installed(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
        tmp_path: Path,
    ):
        """The second instance skips the redundant download when the pack"""
        manifest = _make_manifest("2.5.0")
        version = "2.5.0"
        # The pack is already fully installed at <root>/<version>/ —
        pack_dir = tmp_path / version
        pack_dir.mkdir(parents=True)
        (pack_dir / "worker.exe").write_bytes(b"worker")
        (pack_dir / "pack-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        download_called = threading.Event()

        def fake_download(url, dest, *, expected_sha256, version, event_bus, http_get=None):
            download_called.set()
            return True

        monkeypatch.setattr(
            "voice_typer.server.service.offline_pack.download_offline_pack_with_resume",
            fake_download,
        )

        assert (
            update_check._trigger_background_download(
                manifest=manifest,
                manifest_url=fake_manifest_url,
                config=fake_config_with_consent,
                event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
                root=tmp_path,
                http_get=None,
            )
            is True
        )
        assert not download_called.wait(1.0), (
            "download must NOT run when the pack is already installed at lock-acquisition time"
        )
        # The in-flight guard was still released (the skip is a clean exit).
        deadline = time.monotonic() + 2.0
        while version in update_check._ACTIVE_PACK_DOWNLOADS and time.monotonic() < deadline:
            time.sleep(0.01)
        assert version not in update_check._ACTIVE_PACK_DOWNLOADS

    def test_full_download_and_install_pipeline(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        tmp_path: Path,
    ):
        """End-to-end (mocked network): trigger → download (real resume"""
        import io
        import zipfile

        from voice_typer.server.service import offline_pack

        version = "9.9.9"
        files = {
            "worker.exe": b"worker-binary",
            "engines/parakeet.onnx": b"onnx-weights",
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, blob in files.items():
                zf.writestr(name, blob)
        pack_bytes = buf.getvalue()
        manifest = {
            "version": version,
            "sha256": hashlib.sha256(pack_bytes).hexdigest(),
            "files": [
                {"name": name, "sha256": hashlib.sha256(blob).hexdigest(), "size": len(blob)}
                for name, blob in files.items()
            ],
            "min_proto_version": 1,
        }

        def fake_transport(url, *, offset=0):
            body = pack_bytes[offset:]
            return {
                "status": 200,
                "content_length": len(pack_bytes),
                "iter_chunks": lambda chunk_bytes: (
                    body[i : i + chunk_bytes] for i in range(0, len(body), chunk_bytes)
                ),
            }

        assert (
            update_check._trigger_background_download(
                manifest=manifest,
                manifest_url=fake_manifest_url,
                config=fake_config_with_consent,
                event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
                root=tmp_path,
                http_get=fake_transport,
            )
            is True
        )
        pack_dir = tmp_path / version
        deadline = time.monotonic() + 5.0
        while not (pack_dir / "pack-manifest.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (pack_dir / "pack-manifest.json").exists(), "pack was never installed"
        # The consumed archive is gone (mirrors the NSIS installer).
        assert not offline_pack.offline_pack_partial_path(version, root=tmp_path).exists()
        # Launch-time scan now finds the pack → no update re-trigger.
        assert update_check._local_offline_pack_version(root=tmp_path) == version
        # Renderer contract: offline_pack_verified carries {version, sha256}.
        verified = [e for e in fake_event_bus.events if e["type"] == "offline_pack_verified"]
        assert verified and verified[0]["data"] == {
            "version": version,
            "sha256": manifest["sha256"],
        }


class TestTriggerGuardLeak:
    """A failure AFTER guard registration discards the registration —"""

    def test_mkdir_failure_discards_guard(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
        tmp_path: Path,
    ):
        manifest = _make_manifest("2.2.2")
        dest = update_check.offline_pack.offline_pack_partial_path("2.2.2", root=tmp_path)
        real_mkdir = Path.mkdir
        failures = {"n": 0}

        def fake_mkdir(self, *args, **kwargs):
            if self == dest.parent:
                failures["n"] += 1
                if failures["n"] == 1:
                    raise OSError("simulated disk failure at mkdir")
            return real_mkdir(self, *args, **kwargs)

        download_called = threading.Event()

        def fake_download(url, dest, *, expected_sha256, version, event_bus, http_get=None):
            download_called.set()
            return True

        monkeypatch.setattr(
            "voice_typer.server.service.offline_pack.download_offline_pack_with_resume",
            fake_download,
        )
        monkeypatch.setattr(Path, "mkdir", fake_mkdir)

        # First trigger: mkdir raises → the exception propagates...
        with pytest.raises(OSError):
            update_check._trigger_background_download(
                manifest=manifest,
                manifest_url=fake_manifest_url,
                config=fake_config_with_consent,
                event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
                root=tmp_path,
                http_get=None,
            )
        # ...and the guard is DISCARDED (not leaked).
        assert "2.2.2" not in update_check._ACTIVE_PACK_DOWNLOADS

        # Second trigger: mkdir succeeds → the download proceeds.
        assert (
            update_check._trigger_background_download(
                manifest=manifest,
                manifest_url=fake_manifest_url,
                config=fake_config_with_consent,
                event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
                root=tmp_path,
                http_get=None,
            )
            is True
        )
        assert download_called.wait(2.0), "second trigger never ran the download"
        # Cleanup: let the daemon thread release the guard.
        deadline = time.monotonic() + 2.0
        while "2.2.2" in update_check._ACTIVE_PACK_DOWNLOADS and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_thread_start_failure_discards_guard(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
        tmp_path: Path,
    ):
        manifest = _make_manifest("3.3.3")

        real_thread_start = threading.Thread.start

        def fake_start(self):
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(threading.Thread, "start", fake_start)

        with pytest.raises(RuntimeError):
            update_check._trigger_background_download(
                manifest=manifest,
                manifest_url=fake_manifest_url,
                config=fake_config_with_consent,
                event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
                root=tmp_path,
                http_get=None,
            )
        assert "3.3.3" not in update_check._ACTIVE_PACK_DOWNLOADS

        # The guard is gone → a later trigger is not "already in flight".
        monkeypatch.setattr(threading.Thread, "start", real_thread_start)
        download_called = threading.Event()

        def fake_download(url, dest, *, expected_sha256, version, event_bus, http_get=None):
            download_called.set()
            return True

        monkeypatch.setattr(
            "voice_typer.server.service.offline_pack.download_offline_pack_with_resume",
            fake_download,
        )
        assert (
            update_check._trigger_background_download(
                manifest=manifest,
                manifest_url=fake_manifest_url,
                config=fake_config_with_consent,
                event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
                root=tmp_path,
                http_get=None,
            )
            is True
        )
        assert download_called.wait(2.0)
        deadline = time.monotonic() + 2.0
        while "3.3.3" in update_check._ACTIVE_PACK_DOWNLOADS and time.monotonic() < deadline:
            time.sleep(0.01)


class TestLocalPackVersionScan:
    """``_local_offline_pack_version``, the launch-time pack scan."""

    @staticmethod
    def _install_min_pack(root: Path, version: str) -> None:
        pack_dir = root / version
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "worker.exe").write_bytes(b"worker")
        import hashlib as _hashlib

        manifest = {
            "version": version,
            "sha256": _hashlib.sha256(b"whatever").hexdigest(),
            "files": [
                {"name": "worker.exe", "sha256": _hashlib.sha256(b"worker").hexdigest(), "size": 6},
            ],
            "min_proto_version": 1,
        }
        (pack_dir / "pack-manifest.json").write_text(json.dumps(manifest))

    def test_scan_finds_installed_pack(self, tmp_path: Path):
        self._install_min_pack(tmp_path, "1.2.3")
        assert update_check._local_offline_pack_version(root=tmp_path) == "1.2.3"

    def test_scan_ignores_staging_and_trash_dirs(self, tmp_path: Path):
        """A crashed install can leave ``<version>.new/`` (with a valid"""
        self._install_min_pack(tmp_path, "1.2.3")
        # Simulate the crashed-install leftovers.
        crashed_staging = tmp_path / "2.0.0.new"
        crashed_staging.mkdir(parents=True)
        (crashed_staging / "worker.exe").write_bytes(b"worker2")
        import hashlib as _hashlib

        stale_manifest = {
            "version": "2.0.0",
            "sha256": _hashlib.sha256(b"whatever").hexdigest(),
            "files": [
                {"name": "worker.exe", "sha256": _hashlib.sha256(b"worker2").hexdigest(), "size": 7},
            ],
            "min_proto_version": 1,
        }
        (crashed_staging / "pack-manifest.json").write_text(json.dumps(stale_manifest))
        (tmp_path / "0.9.0.trash").mkdir()

        assert update_check._local_offline_pack_version(root=tmp_path) == "1.2.3"

    def test_scan_returns_none_when_only_staging_exists(self, tmp_path: Path):
        """Nothing but a crashed staging dir → no local pack (an update"""
        staging = tmp_path / "3.0.0.new"
        staging.mkdir(parents=True)
        assert update_check._local_offline_pack_version(root=tmp_path) is None
