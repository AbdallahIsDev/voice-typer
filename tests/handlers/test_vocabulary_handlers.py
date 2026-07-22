"""Unit tests for ``VocabularyHandlersMixin`` (CR-12).

Covers the 2 vocabulary IPC handlers defined in
``voice_typer/server/handlers/vocabulary_handlers.py``:

- ``_handle_get_vocabulary`` — returns ``{type: vocabulary, data: <result>}``.
- ``_handle_save_vocabulary`` — validates payload size (1 MB cap),
  per-value length (1024 char cap), then delegates to
  ``service.save_vocabulary_with_diff``.

The save_vocabulary handler has THREE distinct validation paths:

1. Non-dict payload → ``{message: save_vocabulary requires data: object}``.
2. Payload > 1 MB → ``{message: vocabulary payload too large (...)}``.
3. Any string value > 1024 chars → ``{message: vocabulary value too long in <cat>.<key> (...)}``.
"""

from __future__ import annotations


class TestGetVocabulary:
    """``_handle_get_vocabulary`` — returns the current vocabulary dict."""

    def test_happy_path_returns_vocabulary_type(self, ipc_server, fake_service):
        fake_service.get_vocabulary.return_value = {
            "entries": [{"word": "hello", "spoken": "hi"}],
        }
        resp = ipc_server._handle_get_vocabulary({}, {})
        assert resp["type"] == "vocabulary"
        assert resp["data"] == {
            "entries": [{"word": "hello", "spoken": "hi"}],
        }
        fake_service.get_vocabulary.assert_called_once_with()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.get_vocabulary.side_effect = RuntimeError("corrupt file")
        resp = ipc_server._handle_get_vocabulary({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestSaveVocabulary:
    """``_handle_save_vocabulary`` — payload size + value length validation."""

    def test_happy_path_returns_ack_with_diff(self, ipc_server, fake_service):
        fake_service.save_vocabulary_with_diff.return_value = {
            "ok": True,
            "added": 2,
            "removed": 0,
        }
        resp = ipc_server._handle_save_vocabulary({"entries": [{"word": "hello", "spoken": "hi"}]}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"ok": True, "added": 2, "removed": 0}
        fake_service.save_vocabulary_with_diff.assert_called_once_with({"entries": [{"word": "hello", "spoken": "hi"}]})

    def test_non_dict_payload_returns_error(self, ipc_server, fake_service):
        """NEW-SEC-011: non-dict ``data`` → explicit error (not silent no-op).

        R4-F5 routed the type check through ``_validate_dict_payload``;
        the helper's non-dict message is ``"data must be an object"``
        (different from the pre-R4-F5 ``"save_vocabulary requires data:
        object"`` message, but the test was updated to assert on the
        ``code`` field — which is the renderer-switchable signal —
        rather than the message text).
        """
        resp = ipc_server._handle_save_vocabulary(["not", "a", "dict"], {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_payload"
        assert "data" in resp["data"]["message"]
        fake_service.save_vocabulary_with_diff.assert_not_called()

    def test_payload_over_1mb_returns_error(self, ipc_server, fake_service):
        """Payload > 1 MB → rejected with size-cap message.

        DoS protection: a 1 GB JSON payload would exhaust disk and
        CPU; a 10 MB "good" value would re-compile regex per
        transcription chunk.
        """
        # Build a payload that serializes to > 1 MB.
        big_value = "x" * (2 * 1024 * 1024)  # 2 MB single string.
        resp = ipc_server._handle_save_vocabulary({"entries": {"word1": big_value}}, {})
        # The size check runs BEFORE the per-value length check, so
        # this hits the size cap (not the value-length cap).
        assert resp["type"] == "error"
        assert "too large" in resp["data"]["message"]
        fake_service.save_vocabulary_with_diff.assert_not_called()

    def test_value_over_1024_chars_returns_error(self, ipc_server, fake_service):
        """A single string value > 1024 chars → rejected (under the 1 MB total cap).

        The per-value cap prevents a single regex pattern from
        blowing up transcription latency.
        """
        # Total payload is well under 1 MB; just one value is too long.
        too_long = "y" * 2000  # 2000 chars > 1024 cap.
        resp = ipc_server._handle_save_vocabulary({"entries": {"word1": too_long}}, {})
        assert resp["type"] == "error"
        assert "too long" in resp["data"]["message"]
        assert "entries.word1" in resp["data"]["message"], "error message must identify the offending category.key"
        fake_service.save_vocabulary_with_diff.assert_not_called()

    def test_value_too_long_in_list_entry_returns_error(self, ipc_server, fake_service):
        """List-form vocabulary entries also have per-value length validation.

        The vocabulary schema supports both dict-of-entries and
        list-of-tuples forms (some categories use one, some the
        other).  Both must reject oversized values.
        """
        too_long = "z" * 2000
        resp = ipc_server._handle_save_vocabulary({"my_list_category": [["word", too_long]]}, {})
        assert resp["type"] == "error"
        assert "too long" in resp["data"]["message"]
        assert "my_list_category" in resp["data"]["message"]
