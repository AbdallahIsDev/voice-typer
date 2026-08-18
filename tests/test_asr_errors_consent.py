"""DE-30 / DE-31 regression tests for the consent-error typing fixes.

This file pins the Group-4 fixes for the two findings:

* **DE-30** — ``ConsentRequiredError`` had no ``provider`` / ``scope``
  attributes, so the IPC layer could not distinguish "HuggingFace
  download consent missing" from "OpenAI cloud-transcription consent
  missing".  The fix added ``provider: str = ""`` and ``scope: str = ""``
  class attributes to the base class plus two typed subclasses
  (``HuggingFaceConsentRequiredError`` with ``provider="huggingface"`` /
  ``scope="download"`` and ``CloudConsentRequiredError`` with
  ``scope="transcribe"`` and a per-instance ``provider`` set in
  ``__init__``).

* **DE-31** — the cloud-engine fallback path
  (``CloudEngine.transcribe_with_fallback``) caught a broad
  ``except Exception`` that swallowed ``ConsentRequiredError`` and
  silently routed the request to the local whisper engine — defeating
  the NEW-PRIV-006 consent gate.  Similarly, the IPC dispatch path
  (TCP + stdin) caught a broad ``except Exception`` that turned the
  consent signal into a generic ``server.internal_error`` toast,
  hiding it from the renderer's consent-dialog logic.  The fix narrows
  the cloud-engine clause to ``(RuntimeError, OSError)`` after an
  explicit ``except ConsentRequiredError: raise`` and inserts a
  dedicated ``except ConsentRequiredError`` handler in BOTH IPC
  dispatch paths (TCP and stdin) BEFORE the generic ``except
  Exception``.

The tests are split into four classes:

1. ``TestConsentRequiredErrorAttributes`` — pins the class-attribute
   defaults and subclass overrides introduced by DE-30.
2. ``TestCloudEngineRaisesCloudConsentRequiredError`` — verifies the
   cloud-engines raise site now raises the typed subclass carrying
   ``provider`` / ``scope``.
3. ``TestTranscribeWithFallbackRespectsConsent`` — verifies the
   narrowed ``except`` clause in
   ``CloudEngine.transcribe_with_fallback`` no longer swallows
   ``ConsentRequiredError`` (DE-31 cloud-side fix).
4. ``TestIpcDispatchConsentRequiredEnvelope`` — end-to-end TCP test
   that a handler raising ``ConsentRequiredError`` produces a
   structured ``consent_required`` envelope (not the generic
   ``server.internal_error`` toast) and keeps the connection alive
   (DE-31 IPC-side fix).
"""

from __future__ import annotations

import json
import socket
import time
from contextlib import suppress
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import IPCServer
from voice_typer.server.tray import AppState

# class-attribute and subclass tests ───────────────────────────


class TestConsentRequiredErrorAttributes:
    """DE-30: ``ConsentRequiredError`` carries ``provider`` / ``scope``
    class attributes so the IPC layer can read them off any instance
    (or subclass instance) without ``isinstance`` branching.
    """

    def test_base_class_has_provider_and_scope_defaults(self):
        from voice_typer.server.asr_errors import ConsentRequiredError

        # Class attributes exist and default to empty string (backward
        # compat for legacy raise sites that don't set them).
        assert ConsentRequiredError.provider == ""
        assert ConsentRequiredError.scope == ""

    def test_base_instance_inherits_empty_defaults(self):
        from voice_typer.server.asr_errors import ConsentRequiredError

        exc = ConsentRequiredError("legacy raise site")
        assert exc.provider == ""
        assert exc.scope == ""
        # str() still works as a RuntimeError.
        assert "legacy raise site" in str(exc)
        assert isinstance(exc, RuntimeError)

    def test_huggingface_subclass_attributes(self):
        from voice_typer.server.asr_errors import (
            ConsentRequiredError,
            HuggingFaceConsentRequiredError,
        )

        assert issubclass(HuggingFaceConsentRequiredError, ConsentRequiredError)
        assert HuggingFaceConsentRequiredError.provider == "huggingface"
        assert HuggingFaceConsentRequiredError.scope == "download"

    def test_huggingface_subclass_instance_attributes(self):
        from voice_typer.server.asr_errors import HuggingFaceConsentRequiredError

        exc = HuggingFaceConsentRequiredError("hf consent missing for model X")
        # Class attributes are visible through the instance.
        assert exc.provider == "huggingface"
        assert exc.scope == "download"
        assert "hf consent missing for model X" in str(exc)
        # isinstance check for the base class still passes — backward
        # compat for legacy ``except ConsentRequiredError`` clauses.
        from voice_typer.server.asr_errors import ConsentRequiredError

        assert isinstance(exc, ConsentRequiredError)

    def test_cloud_subclass_attributes(self):
        from voice_typer.server.asr_errors import (
            CloudConsentRequiredError,
            ConsentRequiredError,
        )

        assert issubclass(CloudConsentRequiredError, ConsentRequiredError)
        # scope is a class attribute (always "transcribe").
        assert CloudConsentRequiredError.scope == "transcribe"
        # provider is per-instance (NOT set at class level) — the class
        # attribute is inherited from the base (empty string).
        assert CloudConsentRequiredError.provider == ""

    def test_cloud_subclass_sets_provider_per_instance(self):
        from voice_typer.server.asr_errors import CloudConsentRequiredError

        # Each cloud provider (openai / groq / deepgram) gets its own
        # ``provider`` value on the instance.
        for provider in ("openai", "groq", "deepgram"):
            exc = CloudConsentRequiredError(
                f"Cloud {provider} consent not given",
                provider=provider,
            )
            assert exc.provider == provider
            assert exc.scope == "transcribe"
            assert provider in str(exc)

    def test_cloud_subclass_accepts_message_only(self):
        """DE-30: ``CloudConsentRequiredError(message)`` still works
        without ``provider=`` kwarg (degrades to empty string for the
        provider attribute — backward-compat for callers that haven't
        been updated yet).
        """
        from voice_typer.server.asr_errors import CloudConsentRequiredError

        exc = CloudConsentRequiredError("cloud consent missing")
        assert exc.provider == ""
        assert exc.scope == "transcribe"
        assert "cloud consent missing" in str(exc)


# cloud_engines raise site uses the typed subclass ─────────────


class TestCloudEngineRaisesCloudConsentRequiredError:
    """DE-30: ``CloudEngine.transcribe`` now raises the typed
    ``CloudConsentRequiredError`` (with ``provider`` set) so the IPC
    layer can surface a provider-specific consent dialog.
    """

    def test_transcribe_raises_cloud_consent_subclass(self):
        import numpy as np
        from voice_typer.server.asr_errors import (
            CloudConsentRequiredError,
            ConsentRequiredError,
        )
        from voice_typer.server.cloud_engines import CloudEngine

        eng = CloudEngine(provider="openai", api_key="sk-test-key", consent_given=False)
        audio = np.zeros(16000, dtype=np.float32)
        with pytest.raises(CloudConsentRequiredError) as exc_info:
            eng.transcribe(audio)

        # The typed subclass is also an instance of the base — so
        # legacy ``except ConsentRequiredError`` clauses still catch it.
        assert isinstance(exc_info.value, ConsentRequiredError)
        # Provider is carried through from the CloudEngine instance.
        assert exc_info.value.provider == "openai"
        assert exc_info.value.scope == "transcribe"

    def test_transcribe_raises_cloud_consent_per_provider(self):
        import numpy as np
        from voice_typer.server.asr_errors import CloudConsentRequiredError
        from voice_typer.server.cloud_engines import CloudEngine

        audio = np.zeros(16000, dtype=np.float32)
        for provider in ("openai", "groq", "deepgram"):
            eng = CloudEngine(provider=provider, api_key="sk-test", consent_given=False)
            with pytest.raises(CloudConsentRequiredError) as exc_info:
                eng.transcribe(audio)
            assert exc_info.value.provider == provider, (
                f"Expected provider={provider!r} on the raised exception, got {exc_info.value.provider!r}"
            )
            assert exc_info.value.scope == "transcribe"


# transcribe_with_fallback no longer swallows consent errors ──


class TestTranscribeWithFallbackRespectsConsent:
    """DE-31: ``CloudEngine.transcribe_with_fallback`` must NOT silently
    fall back to the local engine when the cloud path raised
    ``ConsentRequiredError`` — the consent denial is an intentional user
    action and must propagate to the IPC layer for the consent dialog.
    """

    def test_consent_required_propagates_does_not_fallback(self):
        import numpy as np
        from voice_typer.server.asr_errors import (
            CloudConsentRequiredError,
            ConsentRequiredError,
        )
        from voice_typer.server.cloud_engines import CloudEngine

        # Engine with consent_given=True so the constructor doesn't
        # short-circuit (we want to force the consent error from the
        # patched ``transcribe`` to exercise the new
        # ``except ConsentRequiredError: raise`` clause).
        eng = CloudEngine(provider="openai", api_key="sk-test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)
        local_engine = MagicMock()
        local_engine.transcribe.return_value = "local fallback text"

        def raise_consent(_audio):
            raise CloudConsentRequiredError(
                "Cloud openai consent not given",
                provider="openai",
            )

        with (
            pytest.raises(ConsentRequiredError),
            MagicMock.wraps(eng) if False else _patch_transcribe(eng, raise_consent),
        ):
            eng.transcribe_with_fallback(audio, local_engine=local_engine)

        # The local fallback MUST NOT have been called — consent
        # denial propagates instead of triggering a silent fallback.
        local_engine.transcribe.assert_not_called()

    def test_runtime_error_still_triggers_local_fallback(self):
        """DE-31: the narrowed ``except (RuntimeError, OSError)`` clause
        must STILL trigger the local-engine fallback for genuine
        cloud failures (network outage, 5xx, etc.).  This is the
        original PERF-NEW-010 resilience behavior — we must not
        regress it while narrowing the broad ``except Exception``.
        """
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        eng = CloudEngine(provider="openai", api_key="sk-test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)
        local_engine = MagicMock()
        local_engine.transcribe.return_value = "local fallback text"

        def raise_runtime(_audio):
            raise RuntimeError("cloud network down")

        with _patch_transcribe(eng, raise_runtime):
            result = eng.transcribe_with_fallback(audio, local_engine=local_engine)

        assert result == "local fallback text"
        local_engine.transcribe.assert_called_once()

    def test_oserror_still_triggers_local_fallback(self):
        """DE-31: ``URLError`` (an ``OSError``) — the most common
        cloud-failure mode — must still trigger the local fallback.
        This pins the second half of the narrowed clause
        (``RuntimeError, OSError``).
        """
        from urllib.error import URLError

        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        eng = CloudEngine(provider="groq", api_key="sk-test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)
        local_engine = MagicMock()
        local_engine.transcribe.return_value = "local fallback text"

        def raise_url_error(_audio):
            raise URLError("connection refused")

        with _patch_transcribe(eng, raise_url_error):
            result = eng.transcribe_with_fallback(audio, local_engine=local_engine)

        assert result == "local fallback text"
        local_engine.transcribe.assert_called_once()

    def test_unexpected_exception_does_not_silently_fallback(self):
        """DE-31: a non-(RuntimeError, OSError) exception — e.g. a
        ``TypeError`` from a signature-drift bug — must NOT silently
        trigger the local fallback.  Previously the broad
        ``except Exception`` caught it and the bug was masked by the
        fallback succeeding; now the unexpected exception propagates
        so the programmer error surfaces.
        """
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        eng = CloudEngine(provider="openai", api_key="sk-test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)
        local_engine = MagicMock()
        local_engine.transcribe.return_value = "local fallback text"

        def raise_type_error(_audio):
            raise TypeError("signature drift bug — wrong number of args")

        with (
            pytest.raises(TypeError, match="signature drift bug"),
            _patch_transcribe(eng, raise_type_error),
        ):
            eng.transcribe_with_fallback(audio, local_engine=local_engine)

        # The local fallback MUST NOT have been called — TypeError
        # indicates a programmer error, not a cloud outage.
        local_engine.transcribe.assert_not_called()


class _PatchTranscribe:
    """Context manager that patches ``CloudEngine.transcribe`` on a
    specific instance (rather than the class), so multiple tests can
    run in parallel without stomping each other.
    """

    def __init__(self, engine, side_effect):
        self._engine = engine
        self._side_effect = side_effect
        self._original = None

    def __enter__(self):
        self._original = self._engine.transcribe
        self._engine.transcribe = self._side_effect
        return self._engine

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._engine.transcribe = self._original
        return False


def _patch_transcribe(engine, side_effect):
    return _PatchTranscribe(engine, side_effect)


# IPC dispatch path produces a consent_required envelope ──────
# (mirrors tests/test_ipc_dispatch_errors.py infrastructure)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _send_line(sock: socket.socket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _read_response_line(sock: socket.socket, timeout: float = 2.0) -> dict:
    sock.settimeout(timeout)
    buf = b""
    while b"\n" not in buf:
        try:
            chunk = sock.recv(4096)
        except TimeoutError as exc:
            raise TimeoutError(f"Timed out waiting for response. Got partial: {buf!r}") from exc
        if not chunk:
            raise ConnectionError(f"Server closed connection. Got partial: {buf!r}")
        buf += chunk
    line, _ = buf.split(b"\n", 1)
    return json.loads(line.decode("utf-8"))


def _drain(sock: socket.socket, timeout: float = 0.3) -> list[dict]:
    sock.settimeout(timeout)
    lines: list[dict] = []
    buf = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                if raw.strip():
                    with suppress(json.JSONDecodeError, UnicodeDecodeError):
                        lines.append(json.loads(raw.decode("utf-8")))
    except (TimeoutError, OSError):
        pass
    return lines


class _MockApp:
    """Minimal VoiceTyperApp stub for live TCP dispatch tests.

    Mirrors the same shape as ``_MockApp`` in
    ``tests/test_ipc_dispatch_errors.py``.
    """

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tray = MagicMock()
        self.tray.state = AppState.IDLE

        from voice_typer.server.config import Config

        self.config = Config()
        self.config.hotkey = "<f2>"
        self.config.repaste_hotkey = "<ctrl>+<alt>+v"
        self.config.recording_mode = "toggle"
        self.config.push_to_talk_hotkey = ""
        self.config.esc_cancel_enabled = True
        self.config.model_size = "small.en"
        self.config.asr_backend = "whisper"
        self.config.schema_version = 1
        self.config.theme_mode = "system"

        self._ipc_server: object | None = None
        self._quit_called = False
        self._restart_called = False

        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR_OVERRIDE", str(tmp_path))
        try:
            from voice_typer.server.history_db import HistoryDB

            self.history_db = HistoryDB(db_path=tmp_path / "test_history.db")
        except Exception:
            self.history_db = MagicMock()

        from voice_typer.server.service import VoiceTyperService

        self._service = VoiceTyperService(self)

    def quit_app(self) -> None:
        self._quit_called = True

    def restart_app(self) -> None:
        self._restart_called = True

    @property
    def service(self):  # type: ignore[no-untyped-def]
        return self._service


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    port = _free_port()
    token = "de31-consent-test-token"
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", token)
    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR_OVERRIDE", str(tmp_path))

    app = _MockApp(tmp_path=tmp_path, monkeypatch=monkeypatch)
    server = IPCServer(app)
    app._ipc_server = server
    server.start()
    server.start_tcp(port)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(0.25)
            test_sock.connect(("127.0.0.1", port))
            test_sock.close()
            break
        except (TimeoutError, ConnectionRefusedError, OSError):
            time.sleep(0.02)
    else:
        server.stop()
        pytest.fail(f"IPC server did not start listening on port {port} within 2s")

    yield server, port, token

    server.stop()
    with suppress(Exception):
        if hasattr(app, "history_db") and hasattr(app.history_db, "close"):
            app.history_db.close()
    with suppress(Exception):
        if hasattr(app, "_crash_recovery") and hasattr(app._crash_recovery, "shutdown"):
            app._crash_recovery.shutdown()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if server._tcp_server_socket is None:
            break
        time.sleep(0.02)


@pytest.fixture
def authenticated_client(live_server):
    server, port, token = live_server
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    _send_line(client, {"type": "auth", "token": token})
    _drain(client, timeout=0.3)
    yield client, server
    with suppress(OSError):
        client.close()


class TestIpcDispatchConsentRequiredEnvelope:
    """DE-31: a handler raising ``ConsentRequiredError`` must produce a
    structured ``consent_required`` error envelope (carrying
    ``provider`` / ``scope``) instead of the generic
    ``server.internal_error`` toast — and must NOT tear down the TCP
    connection.
    """

    def test_cloud_consent_error_produces_consent_required_envelope(self, authenticated_client, monkeypatch):
        from voice_typer.server.asr_errors import CloudConsentRequiredError

        client, server = authenticated_client

        def raise_cloud_consent(data, resp):  # noqa: ARG001
            raise CloudConsentRequiredError(
                "Cloud openai consent not given — refusing to send audio.",
                provider="openai",
            )

        monkeypatch.setattr(server, "_handle_get_status", raise_cloud_consent)

        _send_line(client, {"id": 42, "type": "get_status"})
        resp = _read_response_line(client, timeout=2.0)

        assert resp["type"] == "error", f"Expected error envelope, got: {resp}"
        assert resp.get("id") == 42, f"Response id mismatch: {resp}"
        # The consent handler produces ``code: consent_required`` (NOT
        # ``server.internal_error`` — that would hide the consent signal
        # from the renderer's consent-dialog logic).
        assert resp["data"]["code"] == "server.consent_required", f"Expected code=consent_required, got: {resp}"
        # provider / scope are surfaced from the exception so the
        # renderer can show the correct provider-specific dialog.
        assert resp["data"]["provider"] == "openai"
        assert resp["data"]["scope"] == "transcribe"
        # The original exception message is surfaced (consent errors
        # are user-actionable, not internal server leakage).
        assert "consent not given" in resp["data"]["message"]

    def test_huggingface_consent_error_produces_consent_required_envelope(self, authenticated_client, monkeypatch):
        from voice_typer.server.asr_errors import HuggingFaceConsentRequiredError

        client, server = authenticated_client

        def raise_hf_consent(data, resp):  # noqa: ARG001
            raise HuggingFaceConsentRequiredError(
                "HuggingFace consent not given — refusing to download model 'small.en'."
            )

        monkeypatch.setattr(server, "_handle_get_status", raise_hf_consent)

        _send_line(client, {"id": 7, "type": "get_status"})
        resp = _read_response_line(client, timeout=2.0)

        assert resp["type"] == "error"
        assert resp.get("id") == 7
        assert resp["data"]["code"] == "server.consent_required"
        # ``provider`` / ``scope`` come from the subclass's class
        # attributes () — they're NOT set per-instance like
        # ``CloudConsentRequiredError``.
        assert resp["data"]["provider"] == "huggingface"
        assert resp["data"]["scope"] == "download"
        assert "HuggingFace consent not given" in resp["data"]["message"]

    def test_legacy_base_consent_error_still_produces_envelope(self, authenticated_client, monkeypatch):
        """DE-30 backward compat: a legacy ``raise
        ConsentRequiredError("...")`` callsite (no provider/scope set)
        must still produce a ``consent_required`` envelope, with empty
        ``provider`` / ``scope`` strings — so the IPC layer's
        ``getattr(exc, "provider", "")`` reads degrade gracefully on
        older raise sites that haven't been migrated to the typed
        subclasses yet (e.g. transcription.py / parakeet_engine.py
        before Agent 2-E adopts them).
        """
        from voice_typer.server.asr_errors import ConsentRequiredError

        client, server = authenticated_client

        def raise_legacy_consent(data, resp):  # noqa: ARG001
            raise ConsentRequiredError("legacy consent raise site")

        monkeypatch.setattr(server, "_handle_get_status", raise_legacy_consent)

        _send_line(client, {"id": 99, "type": "get_status"})
        resp = _read_response_line(client, timeout=2.0)

        assert resp["type"] == "error"
        assert resp.get("id") == 99
        assert resp["data"]["code"] == "server.consent_required"
        # provider / scope degrade to empty string for legacy raise
        # sites (base-class default).
        assert resp["data"]["provider"] == ""
        assert resp["data"]["scope"] == ""
        assert "legacy consent raise site" in resp["data"]["message"]

    def test_consent_error_does_not_mask_as_internal_error(self, authenticated_client, monkeypatch):
        """DE-31 regression: the ``except ConsentRequiredError`` clause
        MUST come BEFORE the generic ``except Exception`` — otherwise
        the consent signal would be swallowed into a generic
        ``server.internal_error`` toast.  This test pins the clause
        ordering by asserting that a ``ConsentRequiredError`` produces
        ``code=consent_required`` (NOT ``code=server.internal_error``).
        """
        from voice_typer.server.asr_errors import CloudConsentRequiredError

        client, server = authenticated_client

        def raise_consent(data, resp):  # noqa: ARG001
            raise CloudConsentRequiredError("groq consent", provider="groq")

        monkeypatch.setattr(server, "_handle_get_status", raise_consent)

        _send_line(client, {"id": 5, "type": "get_status"})
        resp = _read_response_line(client, timeout=2.0)

        # The deciding assertion: code is consent_required, NOT
        # server.internal_error — the consent handler ran first.
        assert resp["data"]["code"] != "server.internal_error", (
            f"ConsentRequiredError was swallowed by the generic except "
            f"Exception clause — clause ordering is wrong: {resp}"
        )
        assert resp["data"]["code"] == "server.consent_required"
        assert resp["data"]["provider"] == "groq"

    def test_connection_survives_consent_error(self, authenticated_client, monkeypatch):
        """DE-31: after a ``consent_required`` envelope, the same TCP
        socket must accept and respond to a subsequent request — the
        connection survives (mirrors the B-6 contract for the generic
        dispatch safety net).
        """
        from voice_typer.server.asr_errors import HuggingFaceConsentRequiredError

        client, server = authenticated_client

        original = server._handle_get_status
        call_count = {"n": 0}

        def consent_then_ok(data, resp):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise HuggingFaceConsentRequiredError("hf consent missing")
            return original(data, resp)

        monkeypatch.setattr(server, "_handle_get_status", consent_then_ok)

        # First call — consent required envelope.
        _send_line(client, {"id": 1, "type": "get_status"})
        resp1 = _read_response_line(client, timeout=2.0)
        assert resp1["type"] == "error"
        assert resp1["data"]["code"] == "server.consent_required"

        # Second call on the SAME socket — connection must survive
        # and the handler (now un-flaked) returns a normal status.
        _send_line(client, {"id": 2, "type": "get_status"})
        resp2 = _read_response_line(client, timeout=2.0)
        assert resp2["type"] == "status", (
            f"Second response should be a normal status — connection "
            f"did not survive the prior consent_required envelope: {resp2}"
        )
        assert resp2.get("id") == 2
