"""Forwarding tests for the real transcribe_offline handler (master plan §7.4)."""

from __future__ import annotations

import sys
import types

import pytest
from voice_typer.server import worker_pending

from tests.fixtures.ipc_test_helpers import make_bare_ipc_server

pytestmark = pytest.mark.xdist_group("ipc_layer_fixes")

_VALID = {"audio_path": "C:\\tmp\\clip.wav", "sample_rate": 16000, "language": None}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    worker_pending.clear_pending()
    monkeypatch.setattr(
        "voice_typer.server.service.update_check._local_offline_pack_version",
        lambda: "1.0",
    )
    yield
    worker_pending.clear_pending()


def _dispatch(payload):
    return make_bare_ipc_server()._dispatch({"id": 7, "type": "transcribe_offline", "data": payload})


def _install_worker_client(monkeypatch, *, ready=True, fail_send=False):
    mod = types.ModuleType("voice_typer.server.worker_client")
    calls: list[dict] = []

    class _FakeClient:
        def send_transcribe(self, audio_path, sample_rate=None, language=None):
            if fail_send:
                raise RuntimeError("socket down")
            if not ready:
                return None
            calls.append({"audio_path": audio_path, "sample_rate": sample_rate, "language": language})
            return 1

    mod.get_shared_client = lambda: _FakeClient()
    monkeypatch.setitem(sys.modules, "voice_typer.server.worker_client", mod)
    import voice_typer.server as _server_pkg

    monkeypatch.setattr(_server_pkg, "worker_client", mod, raising=False)
    return calls


def test_pack_missing_returns_degraded_not_queued(monkeypatch):
    monkeypatch.setattr(
        "voice_typer.server.service.update_check._local_offline_pack_version",
        lambda: None,
    )
    resp = _dispatch(dict(_VALID))
    assert resp["type"] == "ack"
    assert resp["data"]["queued"] is False
    assert resp["data"]["degraded"] is True
    assert resp["data"]["reason"] == "offline_pack_missing"
    assert worker_pending.pending_depth() == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        None,
        "x.wav",
        {"audio_path": "", "sample_rate": 16000, "language": None},
        {"audio_path": "  ", "sample_rate": 16000, "language": None},
        {"audio_path": 123, "sample_rate": 16000, "language": None},
        {"audio_path": "a.wav", "sample_rate": 0, "language": None},
        {"audio_path": "a.wav", "sample_rate": -16000, "language": None},
        {"audio_path": "a.wav", "sample_rate": True, "language": None},
        {"audio_path": "a.wav", "sample_rate": "16000", "language": None},
        {"audio_path": "a.wav", "sample_rate": 16.0, "language": None},
        {"audio_path": "a.wav", "sample_rate": 16000, "language": 123},
        {"audio_path": "a.wav", "language": None},
    ],
)
def test_malformed_payload_rejected_without_raise(payload):
    resp = _dispatch(payload)
    assert resp["type"] == "error"
    assert "code" in resp["data"]
    assert worker_pending.pending_depth() == 0


def test_forward_when_worker_ready(monkeypatch):
    calls = _install_worker_client(monkeypatch, ready=True)
    resp = _dispatch(dict(_VALID))
    assert resp["type"] == "ack"
    assert resp["data"] == {"queued": True, "forwarded": True}
    assert calls == [dict(_VALID)]
    assert worker_pending.pending_depth() == 0


def test_queue_when_worker_not_ready_then_drain(monkeypatch):
    monkeypatch.setitem(sys.modules, "voice_typer.server.worker_client", None)
    resp = _dispatch(dict(_VALID))
    assert resp["type"] == "ack"
    assert resp["data"] == {"queued": True, "forwarded": False, "reason": "worker_not_ready"}
    assert worker_pending.pending_depth() == 1
    calls = _install_worker_client(monkeypatch, ready=True)
    sent = worker_pending.drain_pending(worker_pending.try_forward)
    assert sent == 1
    assert calls == [dict(_VALID)]
    assert worker_pending.pending_depth() == 0


def test_ready_false_queues_without_send(monkeypatch):
    calls = _install_worker_client(monkeypatch, ready=False)
    resp = _dispatch(dict(_VALID))
    assert resp["data"]["queued"] is True
    assert resp["data"]["reason"] == "worker_not_ready"
    assert calls == []
    assert worker_pending.pending_depth() == 1


def test_send_exception_queues_never_raises(monkeypatch):
    _install_worker_client(monkeypatch, ready=True, fail_send=True)
    resp = _dispatch(dict(_VALID))
    assert resp["type"] == "ack"
    assert resp["data"]["queued"] is True
    assert worker_pending.pending_depth() == 1
