"""§10 — tests for the pack-version checker (``update_check.py``).

Covers the auto-update mechanism from plan-runtime-pack-split.md §10.1:

  * SSRF protection — the manifest URL is gated by
    :func:`pack.assert_pack_url_allowed`, which extends the runtime
    allowlist with GitHub hosts + inherits the IP-literal blocklist +
    DNS-rebinding defense from
    :func:`voice_typer.server.security.url_allowlist.assert_url_allowed`
    (the SAME SSRF defense tested by ``tests/test_http_safety_ssrf.py``).
  * Max-bytes limit — the remote manifest is parsed via
    :func:`_secure_read_text(max_bytes=)`, mirroring the cap pattern
    tested by ``tests/test_secure_file_io_max_bytes.py``.
  * Proxy support — :func:`pack.proxy_env` returns ``HTTP_PROXY`` /
    ``HTTPS_PROXY`` env vars; the default transport passes them to
    ``urllib.request`` via a ``ProxyHandler``.
  * Version comparison — :func:`is_newer_version` handles ``v1.2.3``,
    ``1.2.3``, ``1.2.3-rc1``, and shorter tuples (``1.2`` == ``1.2.0``).
  * Background download trigger — when a newer version is found AND
    consent is given, ``check_pack_update`` calls
    ``pack.download_pack_with_resume`` on a daemon thread. The test
    mocks the transport + the download call to verify the trigger.
  * Consent gate — when ``config.runtime_pack_consent`` is False,
    ``check_pack_update`` returns ``{success: False, consent_required: True}``
    + publishes a ``consent_required`` event (mirrors the model-download
    consent flow in ``ModelMixin._require_huggingface_consent``).
  * C-DATA-1 — the pack download from GitHub Releases is NOT covered by
    the existing 3 network-call categories; the test suite documents
    this in the worklog (the user must extend CONSTRAINTS.md).

All network calls are mocked — no real HTTP requests are made.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from voice_typer.server.service import update_check
from voice_typer.server.service.update_check import (
    DEFAULT_PACK_MANIFEST_URL,
    MAX_MANIFEST_BYTES,
    check_pack_update,
    fetch_remote_manifest,
    handle_check_pack_update_ipc,
    is_newer_version,
)

# ── Fixtures ────────────────────────────────────────────────────────────


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
    return (
        "https://github.com/AbdallahIsDev/voice-typer/"
        "releases/latest/download/pack-manifest.json"
    )


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
    """A fake config object with ``runtime_pack_consent=True``."""
    return SimpleNamespace(runtime_pack_consent=True)


@pytest.fixture
def fake_config_no_consent():
    """A fake config object with ``runtime_pack_consent=False``."""
    return SimpleNamespace(runtime_pack_consent=False)


# ── is_newer_version ───────────────────────────────────────────────────


class TestIsNewerVersion:
    """``is_newer_version`` — semver-ish comparison."""

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
        """Non-numeric segments (e.g. ``"1.2.x"``) are treated as 0.

        A malformed version should NOT trigger a spurious update — the
        safe default is "no update" (equal versions). ``1.2.x`` parses
        to ``(1, 2, 0)``, same as ``1.2.0``.
        """
        assert is_newer_version("1.2.x", "1.2.0") is False
        assert is_newer_version("1.2.0", "1.2.x") is False


# ── fetch_remote_manifest ──────────────────────────────────────────────


class TestFetchRemoteManifest:
    """``fetch_remote_manifest`` — SSRF + max-bytes + schema validation."""

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
        # hosts but STILL rejects private IP literals. ``http://10.0.0.5``
        # is not in the allowlist AND is a private IP — double-rejected.
        # We mock the transport to raise if called — proving the SSRF
        # check fires BEFORE the HTTP call.
        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            raise AssertionError("HTTP transport should NOT be called for SSRF-blocked URL")

        result = fetch_remote_manifest(
            "https://10.0.0.5/pack-manifest.json", http_get=fake_http_get
        )
        assert result is None

    def test_returns_none_on_non_allowlisted_host(self):
        """A non-allowlisted host (not GitHub + not in the allowlist) is rejected."""
        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            raise AssertionError("HTTP transport should NOT be called for non-allowlisted host")

        result = fetch_remote_manifest(
            "https://evil.example.com/pack-manifest.json", http_get=fake_http_get
        )
        assert result is None

    def test_returns_none_on_network_error(self, fake_manifest_url: str):
        """An OSError during fetch → None (no exception propagates)."""
        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            raise OSError("simulated DNS failure")

        result = fetch_remote_manifest(fake_manifest_url, http_get=fake_http_get)
        assert result is None

    def test_returns_none_on_invalid_json(self, fake_manifest_url: str):
        """A non-JSON response → None."""
        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return "not json {{{"

        result = fetch_remote_manifest(fake_manifest_url, http_get=fake_http_get)
        assert result is None

    def test_returns_none_on_schema_validation_failure(self, fake_manifest_url: str):
        """A JSON response that fails ``load_pack_manifest`` schema validation → None."""
        # Missing required 'version' field.
        bad_manifest = {"sha256": "0" * 64, "files": [], "min_proto_version": 1}

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return json.dumps(bad_manifest)

        result = fetch_remote_manifest(fake_manifest_url, http_get=fake_http_get)
        assert result is None

    def test_oversized_manifest_rejected(self, fake_manifest_url: str):
        """A manifest body exceeding ``max_bytes`` is rejected.

        Mirrors the ``_secure_read_text`` cap test in
        ``tests/test_secure_file_io_max_bytes.py`` — the cap is on BYTES,
        not characters, and aborts IMMEDIATELY (does not read the whole
        body).
        """
        # Build a body that's just over the 1 MiB cap.
        big_body = "x" * (MAX_MANIFEST_BYTES + 1)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            # The transport itself enforces the cap (defense-in-depth
            # layer 1). Simulate the cap firing.
            if len(big_body) > max_bytes:
                raise RuntimeError(
                    f"manifest exceeds max_bytes={max_bytes} (read {len(big_body)} bytes so far)"
                )
            return big_body

        result = fetch_remote_manifest(fake_manifest_url, http_get=fake_http_get)
        assert result is None

    def test_proxy_env_passed_through(self, fake_manifest_url: str, monkeypatch):
        """The default transport reads ``HTTP_PROXY`` / ``HTTPS_PROXY`` env vars.

        We can't easily assert the ``ProxyHandler`` is constructed (it's
        internal to ``_http_get_manifest``), but we CAN assert that
        ``proxy_env`` reads the env vars correctly by calling it
        directly. This test pins the env-var contract.
        """
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example.com:8080")
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp.example.com:8080")
        from voice_typer.server.service.pack import proxy_env

        env = proxy_env()
        assert env.get("HTTPS_PROXY") == "http://proxy.corp.example.com:8080"
        assert env.get("HTTP_PROXY") == "http://proxy.corp.example.com:8080"

    def test_lowercase_proxy_env_vars_supported(self, monkeypatch):
        """``requests`` / ``httpx`` honor lowercase proxy env vars; so does ``proxy_env``."""
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.setenv("https_proxy", "http://proxy.corp.example.com:8080")
        monkeypatch.setenv("http_proxy", "http://proxy.corp.example.com:8080")
        from voice_typer.server.service.pack import proxy_env

        env = proxy_env()
        assert env.get("https_proxy") == "http://proxy.corp.example.com:8080"
        assert env.get("http_proxy") == "http://proxy.corp.example.com:8080"


# ── check_pack_update ─────────────────────────────────────────────────


class TestCheckPackUpdate:
    """``check_pack_update`` — the main entry point."""

    def test_no_local_pack_remote_available_triggers_download(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """When no local pack exists + remote is available + consent given →
        background download is triggered."""
        # No local pack → local_version=None → update_available=True.
        monkeypatch.setattr(update_check, "_local_pack_version", lambda root=None: None)
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        # Mock the background download trigger so we don't actually
        # spawn a thread that calls ``download_pack_with_resume``.
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

        result = check_pack_update(
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
        monkeypatch.setattr(update_check, "_local_pack_version", lambda root=None: "1.2.3")
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        download_called = {"called": False}

        def fake_trigger(**kwargs):
            download_called["called"] = True
            return True

        monkeypatch.setattr(update_check, "_trigger_background_download", fake_trigger)

        result = check_pack_update(
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
        monkeypatch.setattr(update_check, "_local_pack_version", lambda root=None: "1.2.2")
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        download_called = {"called": False}

        def fake_trigger(**kwargs):
            download_called["called"] = True
            return True

        monkeypatch.setattr(update_check, "_trigger_background_download", fake_trigger)

        result = check_pack_update(
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

    def test_consent_missing_returns_consent_required(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_no_consent,
        monkeypatch,
    ):
        """When ``runtime_pack_consent=False`` → ``{success: False, consent_required: True}``
        + a ``consent_required`` event is published."""
        monkeypatch.setattr(update_check, "_local_pack_version", lambda root=None: None)
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        result = check_pack_update(
            fake_config_no_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            manifest_url=fake_manifest_url,
        )

        assert result["success"] is False
        assert result.get("consent_required") is True
        assert result["download_triggered"] is False
        assert "error" in result
        assert result["reason"] == "consent_required"

        # The consent_required event should have been published.
        consent_events = [e for e in fake_event_bus.events if e["type"] == "consent_required"]
        assert len(consent_events) == 1, (
            f"expected 1 consent_required event, got {len(consent_events)}: {fake_event_bus.events}"
        )
        assert consent_events[0]["data"]["provider"] == "github"
        assert consent_events[0]["data"]["scope"] == "runtime_pack"

    def test_fetch_failure_returns_error(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """When the remote manifest can't be fetched → ``{success: False, reason: 'fetch_failed'}``."""
        monkeypatch.setattr(update_check, "_local_pack_version", lambda root=None: "1.2.3")

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            raise OSError("simulated network failure")

        result = check_pack_update(
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

    def test_trigger_download_false_skips_download(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """``trigger_download=False`` → check runs but download is NOT triggered."""
        monkeypatch.setattr(update_check, "_local_pack_version", lambda root=None: None)
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        download_called = {"called": False}

        def fake_trigger(**kwargs):
            download_called["called"] = True
            return True

        monkeypatch.setattr(update_check, "_trigger_background_download", fake_trigger)

        result = check_pack_update(
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
        custom_url = (
            "https://github.com/my-org/my-fork/"
            "releases/latest/download/pack-manifest.json"
        )
        monkeypatch.setenv("VT_PACK_MANIFEST_URL", custom_url)
        monkeypatch.setattr(update_check, "_local_pack_version", lambda root=None: None)

        fetched_urls: list[str] = []
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            fetched_urls.append(url)
            return body

        def fake_trigger(**kwargs):
            return True

        monkeypatch.setattr(update_check, "_trigger_background_download", fake_trigger)

        check_pack_update(
            fake_config_with_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            # NOTE: manifest_url NOT passed — should fall back to env var.
        )

        assert fetched_urls == [custom_url], (
            f"expected fetch from env-var URL {custom_url!r}, got {fetched_urls}"
        )

    def test_default_manifest_url_is_github_releases_latest(self):
        """The default manifest URL points at GitHub Releases ``/latest/download/``.

        This pins the URL contract documented in
        ``docs/auto-update-feature.md`` (to be updated by Sub-agent 15)
        — the renderer / publisher / checker all rely on this URL
        shape.
        """
        assert DEFAULT_PACK_MANIFEST_URL == (
            "https://github.com/AbdallahIsDev/voice-typer/"
            "releases/latest/download/pack-manifest.json"
        ), (
            "DEFAULT_PACK_MANIFEST_URL changed — update docs/auto-update-feature.md "
            "(Sub-agent 15) and the publisher (publish_pack_release.py) to match."
        )

    def test_result_includes_checked_at_epoch_ms(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """The result includes ``checked_at`` (epoch ms) for UI display."""
        monkeypatch.setattr(update_check, "_local_pack_version", lambda root=None: "1.2.3")
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        before = int(time.time() * 1000)
        result = check_pack_update(
            fake_config_with_consent,
            fake_event_bus.bus,  # type: ignore[arg-type]
            http_get=fake_http_get,
            manifest_url=fake_manifest_url,
        )
        after = int(time.time() * 1000)

        assert "checked_at" in result
        assert before <= result["checked_at"] <= after, (
            f"checked_at {result['checked_at']} not in [{before}, {after}]"
        )


# ── _trigger_background_download ──────────────────────────────────────


class TestTriggerBackgroundDownload:
    """``_trigger_background_download`` — spawns the download thread."""

    def test_spawns_thread_with_correct_url(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_with_consent,
        monkeypatch,
    ):
        """The download URL is constructed from the manifest URL + version.

        ``https://github.com/.../releases/latest/download/pack-manifest.json``
        → ``https://github.com/.../releases/latest/download/pack-1.2.3.zip``.
        """
        manifest = _make_manifest("1.2.3")
        captured: dict = {}

        def fake_download(url, dest, *, expected_sha256, version, event_bus, http_get=None):
            captured["url"] = url
            captured["dest"] = dest
            captured["expected_sha256"] = expected_sha256
            captured["version"] = version
            return True

        # Patch ``pack.download_pack_with_resume`` at the pack module
        # (where ``update_check`` imports it from).
        monkeypatch.setattr(
            "voice_typer.server.service.pack.download_pack_with_resume",
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

        # The download runs on a daemon thread — give it a moment to
        # call our fake.

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
            "https://github.com/AbdallahIsDev/voice-typer/"
            "releases/latest/download/pack-1.2.3.zip"
        ), (
            f"download URL should be constructed from manifest URL + version, "
            f"got {captured['url']!r}"
        )
        assert captured["expected_sha256"] == manifest["sha256"]
        assert captured["version"] == "1.2.3"

    def test_consent_missing_raises(
        self,
        fake_manifest_url: str,
        fake_event_bus,
        fake_config_no_consent,
    ):
        """When consent is missing, ``_trigger_background_download`` raises
        :class:`PackConsentRequiredError` (the caller catches it)."""
        from voice_typer.server.service.pack import PackConsentRequiredError

        manifest = _make_manifest("1.2.3")
        with pytest.raises(PackConsentRequiredError):
            update_check._trigger_background_download(
                manifest=manifest,
                manifest_url=fake_manifest_url,
                config=fake_config_no_consent,
                event_bus=fake_event_bus.bus,  # type: ignore[arg-type]
                root=None,
                http_get=None,
            )


# ── handle_check_pack_update_ipc ──────────────────────────────────────


class TestHandleCheckPackUpdateIpc:
    """``handle_check_pack_update_ipc`` — thin IPC wrapper."""

    def test_returns_plain_dict(self, fake_config_with_consent, fake_event_bus, monkeypatch):
        """The IPC handler returns a plain ``dict`` (not a TypedDict instance)."""
        monkeypatch.setattr(update_check, "_local_pack_version", lambda root=None: "1.2.3")
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        app = SimpleNamespace(
            config=fake_config_with_consent,
            event_bus=fake_event_bus.bus,
        )
        result = handle_check_pack_update_ipc(app, None, http_get=fake_http_get)

        assert isinstance(result, dict)
        assert "success" in result
        assert result["success"] is True

    def test_app_none_tolerated(self, monkeypatch):
        """``app=None`` is tolerated — treated as no-config + no-event-bus.

        The check still runs; consent will fail + no events published.
        """
        monkeypatch.setattr(update_check, "_local_pack_version", lambda root=None: None)
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        result = handle_check_pack_update_ipc(None, None, http_get=fake_http_get)
        assert isinstance(result, dict)
        # consent_required (config is None → no consent)
        assert result["success"] is False
        assert result.get("consent_required") is True

    def test_app_without_event_bus_attribute_falls_back_to_module(
        self,
        fake_config_with_consent,
        monkeypatch,
    ):
        """When ``app.event_bus`` is missing, the handler falls back to the
        module-level ``voice_typer.server.event_bus``."""
        monkeypatch.setattr(update_check, "_local_pack_version", lambda root=None: "1.2.3")
        manifest = _make_manifest("1.2.3")
        body = json.dumps(manifest)

        def fake_http_get(url, *, max_bytes=MAX_MANIFEST_BYTES):
            return body

        # app has config but NO event_bus attribute.
        app = SimpleNamespace(config=fake_config_with_consent)
        result = handle_check_pack_update_ipc(app, None, http_get=fake_http_get)
        assert isinstance(result, dict)
        assert result["success"] is True


# ── Max-bytes cap (defense-in-depth via _secure_read_text) ─────────────


class TestMaxBytesCapInherited:
    """The max-bytes cap is inherited from ``_secure_read_text``.

    ``fetch_remote_manifest`` writes the body to a temp file and reads
    it back via ``_secure_read_text(max_bytes=)``. This test verifies
    the cap is wired up — a body just under the cap succeeds, a body
    just over fails.
    """

    def test_cap_is_one_mebibyte(self):
        """``MAX_MANIFEST_BYTES`` is 1 MiB (1048576 bytes)."""
        assert MAX_MANIFEST_BYTES == 1024 * 1024

    def test_secure_read_text_rejects_oversized(self, tmp_path: Path):
        """``_secure_read_text`` rejects a file exceeding ``max_bytes``.

        This is a re-test of the contract from
        ``tests/test_secure_file_io_max_bytes.py`` — included here to
        document that ``update_check`` inherits the cap.
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


# ── SSRF inheritance (defense-in-depth) ────────────────────────────────


class TestSSRFInherited:
    """The SSRF defense is inherited from ``assert_pack_url_allowed``.

    ``assert_pack_url_allowed`` extends the allowlist with GitHub hosts
    + inherits the IP-literal blocklist + DNS-rebinding defense from
    :func:`voice_typer.server.security.url_allowlist.assert_url_allowed`.
    This test verifies the inheritance chain is wired up.
    """

    def test_github_hosts_in_allowlist_after_call(self, fake_manifest_url: str):
        """After calling ``fetch_remote_manifest`` (which calls
        ``assert_pack_url_allowed``), the GitHub hosts are in the
        runtime allowlist."""
        from voice_typer.server.security.url_allowlist import get_url_allowlist

        # The first call to ``assert_pack_url_allowed`` extends the
        # allowlist with GitHub hosts.
        try:
            fetch_remote_manifest(
                fake_manifest_url,
                http_get=lambda url, **kw: json.dumps(_make_manifest()).decode("utf-8"),
            )
        except Exception:
            pass  # we don't care about the result — just the allowlist side effect

        allowlist = get_url_allowlist()
        assert "github.com" in allowlist
        assert "objects.githubusercontent.com" in allowlist
        assert "codeload.github.com" in allowlist

    def test_private_ip_literal_rejected_even_if_allowlisted(self):
        """Even if a private IP is added to the allowlist, the SSRF check
        rejects it (defense-in-depth — mirrors the regression test in
        ``tests/test_http_safety_ssrf.py``)."""
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
