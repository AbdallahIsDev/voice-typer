"""DE-30 / DE-31 regression tests for the consent-error typing fixes."""

from __future__ import annotations

import json
import socket
from contextlib import suppress
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.tray import AppState


class TestConsentRequiredErrorAttributes:
    """DE-30: ``ConsentRequiredError`` carries ``provider`` / ``scope``"""

    def test_base_class_has_provider_and_scope_defaults(self):
        from voice_typer.server.asr_errors import ConsentRequiredError

        # Class attributes exist and default to empty string (backward
        assert ConsentRequiredError.provider == ""
        assert ConsentRequiredError.scope == ""

    def test_base_instance_inherits_empty_defaults(self):
        from voice_typer.server.asr_errors import ConsentRequiredError

        exc = ConsentRequiredError("legacy raise site")
        assert exc.provider == ""
        assert exc.scope == ""
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
        from voice_typer.server.asr_errors import ConsentRequiredError

        assert isinstance(exc, ConsentRequiredError)

    def test_cloud_subclass_attributes(self):
        from voice_typer.server.asr_errors import (
            CloudConsentRequiredError,
            ConsentRequiredError,
        )

        assert issubclass(CloudConsentRequiredError, ConsentRequiredError)
        assert CloudConsentRequiredError.scope == "transcribe"
        assert CloudConsentRequiredError.provider == ""

    def test_cloud_subclass_sets_provider_per_instance(self):
        from voice_typer.server.asr_errors import CloudConsentRequiredError

        # Each cloud provider (openai / groq / deepgram) gets its own
        for provider in ("openai", "groq", "deepgram"):
            exc = CloudConsentRequiredError(
                f"Cloud {provider} consent not given",
                provider=provider,
            )
            assert exc.provider == provider
            assert exc.scope == "transcribe"
            assert provider in str(exc)

    def test_cloud_subclass_accepts_message_only(self):
        """DE-30: ``CloudConsentRequiredError(message)`` still works"""
        from voice_typer.server.asr_errors import CloudConsentRequiredError

        exc = CloudConsentRequiredError("cloud consent missing")
        assert exc.provider == ""
        assert exc.scope == "transcribe"
        assert "cloud consent missing" in str(exc)


class TestCloudEngineRaisesCloudConsentRequiredError:
    """DE-30: ``CloudEngine.transcribe`` now raises the typed"""

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


class TestTranscribeWithFallbackRespectsConsent:
    """DE-31: ``CloudEngine.transcribe_with_fallback`` must NOT silently"""

    def test_consent_required_propagates_does_not_fallback(self):
        import numpy as np
        from voice_typer.server.asr_errors import (
            CloudConsentRequiredError,
            ConsentRequiredError,
        )
        from voice_typer.server.cloud_engines import CloudEngine

        # Engine with consent_given=True so the constructor doesn't
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

        # The local fallback MUST NOT have been called, consent
        local_engine.transcribe.assert_not_called()

    def test_runtime_error_still_triggers_local_fallback(self):
        """DE-31: the narrowed ``except (RuntimeError, OSError)`` clause"""
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
        """
        DE-31: ``URLError`` (an ``OSError``), the most common
        This pins the second half of the narrowed clause
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
        """``TypeError`` from a signature-drift bug, must NOT silently"""
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        eng = CloudEngine(provider="openai", api_key="sk-test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)
        local_engine = MagicMock()
        local_engine.transcribe.return_value = "local fallback text"

        def raise_type_error(_audio):
            raise TypeError("signature drift bug, wrong number of args")

        with (
            pytest.raises(TypeError, match="signature drift bug"),
            _patch_transcribe(eng, raise_type_error),
        ):
            eng.transcribe_with_fallback(audio, local_engine=local_engine)

        # The local fallback MUST NOT have been called, TypeError
        local_engine.transcribe.assert_not_called()


class _PatchTranscribe:
    """specific instance (rather than the class), so multiple tests can"""

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
    """Minimal VoiceTyperApp stub for live TCP dispatch tests."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tray = MagicMock()
        self.tray.state = AppState.IDLE

        from voice_typer.server.config import Config

        self.config = Config()
        self.config.hotkey = "<f2>"
        self.config.repaste_hotkey = "<ctrl>+<alt>+v"
        self.config.recording_mode = "toggle"
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
def consent_server(tmp_path, monkeypatch):
    """IPCServer with fakes, no TCP transport (WS/stdin only)."""
    from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR_OVERRIDE", str(tmp_path))
    server, _fake_app, _fake_service = make_ipc_server_with_fakes()
    server._running = True
    return server


class TestIpcDispatchConsentRequiredEnvelope:
    """structured ``consent_required`` error envelope (carrying"""

    def test_cloud_consent_error_produces_consent_required_envelope(self, consent_server, monkeypatch):
        from voice_typer.server.asr_errors import CloudConsentRequiredError

        server = consent_server

        def raise_cloud_consent(data, resp):  # noqa: ARG001
            raise CloudConsentRequiredError(
                "Cloud openai consent not given, refusing to send audio.",
                provider="openai",
            )

        monkeypatch.setattr(server, "_handle_get_status", raise_cloud_consent)

        resp = server._dispatch({"id": 42, "type": "get_status"})

        assert resp["type"] == "error", f"Expected error envelope, got: {resp}"
        assert resp.get("id") == 42, f"Response id mismatch: {resp}"
        assert resp["data"]["code"] == "server.consent_required", f"Expected code=consent_required, got: {resp}"
        assert resp["data"]["provider"] == "openai"
        assert resp["data"]["scope"] == "transcribe"
        assert "consent not given" in resp["data"]["message"]

    def test_huggingface_consent_error_produces_consent_required_envelope(self, consent_server, monkeypatch):
        from voice_typer.server.asr_errors import HuggingFaceConsentRequiredError

        server = consent_server

        def raise_hf_consent(data, resp):  # noqa: ARG001
            raise HuggingFaceConsentRequiredError(
                "HuggingFace consent not given, refusing to download model 'small.en'."
            )

        monkeypatch.setattr(server, "_handle_get_status", raise_hf_consent)

        resp = server._dispatch({"id": 7, "type": "get_status"})

        assert resp["type"] == "error"
        assert resp.get("id") == 7
        assert resp["data"]["code"] == "server.consent_required"
        assert resp["data"]["provider"] == "huggingface"
        assert resp["data"]["scope"] == "download"
        assert "HuggingFace consent not given" in resp["data"]["message"]

    def test_legacy_base_consent_error_still_produces_envelope(self, consent_server, monkeypatch):
        """A legacy ``raise ConsentRequiredError(\"...\")`` callsite (no"""
        from voice_typer.server.asr_errors import ConsentRequiredError

        server = consent_server

        def raise_legacy_consent(data, resp):  # noqa: ARG001
            raise ConsentRequiredError("legacy consent raise site")

        monkeypatch.setattr(server, "_handle_get_status", raise_legacy_consent)

        resp = server._dispatch({"id": 99, "type": "get_status"})

        assert resp["type"] == "error"
        assert resp.get("id") == 99
        assert resp["data"]["code"] == "server.consent_required"
        assert resp["data"]["provider"] == ""
        assert resp["data"]["scope"] == ""
        assert "legacy consent raise site" in resp["data"]["message"]

    def test_consent_error_does_not_mask_as_internal_error(self, consent_server, monkeypatch):
        """The ``except ConsentRequiredError`` clause MUST come BEFORE"""
        from voice_typer.server.asr_errors import CloudConsentRequiredError

        server = consent_server

        def raise_consent(data, resp):  # noqa: ARG001
            raise CloudConsentRequiredError("groq consent", provider="groq")

        monkeypatch.setattr(server, "_handle_get_status", raise_consent)

        resp = server._dispatch({"id": 5, "type": "get_status"})

        assert resp["data"]["code"] != "server.internal_error", (
            f"ConsentRequiredError was swallowed by the generic except "
            f"Exception clause, clause ordering is wrong: {resp}"
        )
        assert resp["data"]["code"] == "server.consent_required"
        assert resp["data"]["provider"] == "groq"

    def test_dispatch_survives_consent_error(self, consent_server, monkeypatch):
        """After a ``consent_required`` envelope, the server must still"""
        from voice_typer.server.asr_errors import HuggingFaceConsentRequiredError

        server = consent_server

        original = server._handle_get_status
        call_count = {"n": 0}

        def consent_then_ok(data, resp):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise HuggingFaceConsentRequiredError("hf consent missing")
            return original(data, resp)

        monkeypatch.setattr(server, "_handle_get_status", consent_then_ok)

        resp1 = server._dispatch({"id": 1, "type": "get_status"})
        assert resp1["type"] == "error"
        assert resp1["data"]["code"] == "server.consent_required"

        resp2 = server._dispatch({"id": 2, "type": "get_status"})
        assert resp2["type"] == "status", (
            f"Second response should be a normal status, server "
            f"did not survive the prior consent_required envelope: {resp2}"
        )
        assert resp2.get("id") == 2
