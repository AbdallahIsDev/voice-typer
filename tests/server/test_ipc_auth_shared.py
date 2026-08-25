"""VP-8: shared TCP/WS auth-handshake helpers in ``ipc/auth.py``.

The audit found the TCP and sidecar-WS auth handshakes duplicated the
same contract (~120 LOC) with evidence of drift (TCP rejects a
``protocol_version`` mismatch, WS only warns). The shared piece —
frame-shape validation + constant-time token comparison — was
extracted into ``voice_typer/server/ipc/auth.py``:

- :func:`extract_auth_token` — validates the
  ``{"type": "auth", "token": ...}`` first-frame contract and returns
  the token (or ``None``).
- :func:`tokens_equal` — constant-time ``hmac.compare_digest`` wrapper.

These tests cover the helper contract directly and pin that BOTH
transports route their token handling through the shared module (so a
future "fix in one file" can't silently drift back into two copies).
"""

from __future__ import annotations

import importlib
import inspect

import pytest
from voice_typer.server.ipc.auth import extract_auth_token, tokens_equal


class TestExtractAuthToken:
    """The auth frame must be a dict with ``type == "auth"`` and a
    non-empty str token; anything else yields ``None`` (no comparison)."""

    def test_valid_frame_returns_token(self):
        assert extract_auth_token({"type": "auth", "token": "secret"}) == "secret"

    @pytest.mark.parametrize(
        "frame",
        [
            pytest.param([1, 2, 3], id="list"),
            pytest.param("auth", id="string"),
            pytest.param(42, id="int"),
            pytest.param(None, id="none"),
        ],
    )
    def test_non_dict_returns_none(self, frame):
        assert extract_auth_token(frame) is None

    def test_wrong_type_returns_none(self):
        assert extract_auth_token({"type": "hello", "token": "secret"}) is None

    def test_missing_type_returns_none(self):
        assert extract_auth_token({"token": "secret"}) is None

    def test_missing_token_returns_none(self):
        assert extract_auth_token({"type": "auth"}) is None

    def test_empty_token_returns_none(self):
        assert extract_auth_token({"type": "auth", "token": ""}) is None

    def test_non_str_token_returns_none(self):
        assert extract_auth_token({"type": "auth", "token": 123}) is None


class TestTokensEqual:
    """Constant-time comparison (via ``hmac.compare_digest``)."""

    def test_equal_tokens(self):
        assert tokens_equal("abc", "abc") is True

    def test_unequal_tokens(self):
        assert tokens_equal("abc", "abd") is False

    def test_empty_vs_non_empty(self):
        assert tokens_equal("", "abc") is False

    def test_whitespace_padded_rejected(self):
        # hmac.compare_digest is byte-exact: leading/trailing whitespace
        # must NOT match (a substring / ``in`` comparison would).
        assert tokens_equal(" abc ", "abc") is False


class TestSharedHelperWiredIntoBothTransports:
    """VP-8 anti-drift pin: both transports must route their token
    handling through ``ipc/auth.py`` (a bug fix to the auth contract
    must land in ONE module, not two copies)."""

    def test_tcp_transport_uses_shared_helpers(self):
        src = inspect.getsource(importlib.import_module("voice_typer.server.ipc.transport_tcp"))
        assert "extract_auth_token(" in src, "transport_tcp must call extract_auth_token (shared ipc/auth.py helper)"
        assert "tokens_equal(" in src, "transport_tcp must call tokens_equal (shared ipc/auth.py helper)"

    def test_ws_transport_uses_shared_helpers(self):
        src = inspect.getsource(importlib.import_module("voice_typer.server.sidecar_ws"))
        assert "extract_auth_token(" in src, "sidecar_ws must call extract_auth_token (shared ipc/auth.py helper)"
        assert "tokens_equal(" in src, "sidecar_ws must call tokens_equal (shared ipc/auth.py helper)"
