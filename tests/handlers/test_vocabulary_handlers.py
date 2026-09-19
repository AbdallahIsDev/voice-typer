"""Unit tests for ``VocabularyHandlersMixin`` (CR-12)."""

from __future__ import annotations


class TestGetVocabulary:
    """``_handle_get_vocabulary``, returns the current vocabulary dict."""

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
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestGetCorrectionUsage:
    """``_handle_get_correction_usage``, per-correction usage snapshot."""

    def test_happy_path_returns_correction_usage_type(self, ipc_server, fake_service):
        fake_service.get_correction_usage.return_value = {
            "entries": {"misspellings": {"recieve": {"count": 3, "last_ts": 1723800000.0}}},
            "corrections_by_day": {"2026-08-16": 3},
            "dictations_by_day": {"2026-08-16": 1},
        }
        resp = ipc_server._handle_get_correction_usage({}, {})
        assert resp["type"] == "correction_usage"
        assert resp["data"]["entries"]["misspellings"]["recieve"]["count"] == 3
        fake_service.get_correction_usage.assert_called_once_with()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.get_correction_usage.side_effect = RuntimeError("corrupt file")
        resp = ipc_server._handle_get_correction_usage({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"


class TestSaveVocabulary:
    """``_handle_save_vocabulary``, payload size + value length validation."""

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
        """NEW-SEC-011: non-dict ``data`` → explicit error (not silent no-op)."""
        resp = ipc_server._handle_save_vocabulary(["not", "a", "dict"], {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        assert "data" in resp["data"]["message"]
        fake_service.save_vocabulary_with_diff.assert_not_called()

    def test_payload_over_1mb_returns_error(self, ipc_server, fake_service):
        """Payload > 1 MB → rejected with size-cap message."""
        # Build a payload that serializes to > 1 MB.
        big_value = "x" * (2 * 1024 * 1024)  # 2 MB single string.
        resp = ipc_server._handle_save_vocabulary({"entries": {"word1": big_value}}, {})
        assert resp["type"] == "error"
        assert "too large" in resp["data"]["message"]
        fake_service.save_vocabulary_with_diff.assert_not_called()

    def test_value_over_500_chars_returns_error(self, ipc_server, fake_service):
        """A single string value > 500 chars → rejected (under the 1 MB total cap)."""
        too_long = "y" * 2000  # 2000 chars > 500 cap.
        resp = ipc_server._handle_save_vocabulary({"entries": {"word1": too_long}}, {})
        assert resp["type"] == "error"
        assert "too long" in resp["data"]["message"]
        assert "entries.word1" in resp["data"]["message"], "error message must identify the offending category.key"
        fake_service.save_vocabulary_with_diff.assert_not_called()

    def test_value_at_500_chars_accepted(self, ipc_server, fake_service):
        """XZ-R11-07: a value exactly at the 500-char cap is accepted."""
        ok_value = "y" * 500  # exactly at cap → accepted
        fake_service.save_vocabulary_with_diff.return_value = {"saved": True}
        resp = ipc_server._handle_save_vocabulary({"entries": {"word1": ok_value}}, {})
        assert resp["type"] == "ack"
        fake_service.save_vocabulary_with_diff.assert_called_once()

    def test_value_too_long_in_list_entry_returns_error(self, ipc_server, fake_service):
        """List-form vocabulary entries also have per-value length validation."""
        too_long = "z" * 2000
        resp = ipc_server._handle_save_vocabulary({"my_list_category": [["word", too_long]]}, {})
        assert resp["type"] == "error"
        assert "too long" in resp["data"]["message"]
        assert "my_list_category" in resp["data"]["message"]


class TestSaveVocabularyDuplicateRejection:
    """a structured ``client.duplicate_entry`` envelope so the renderer can"""

    def test_duplicate_entry_returns_structured_envelope(self, ipc_server, fake_service):
        from voice_typer.server.service.vocabulary import VocabularyDuplicateError

        fake_service.save_vocabulary_with_diff.side_effect = VocabularyDuplicateError("to 2", 3)
        resp = ipc_server._handle_save_vocabulary(
            {"phrase_corrections": [["to 2", "to"]]},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.duplicate_entry"
        assert "to 2" in resp["data"]["message"]
        assert resp["data"]["message"].startswith("duplicate correction:")
        fake_service.save_vocabulary_with_diff.assert_called_once()

    def test_other_service_error_uses_generic_envelope(self, ipc_server, fake_service):
        fake_service.save_vocabulary_with_diff.side_effect = RuntimeError("boom")
        resp = ipc_server._handle_save_vocabulary({"entries": {}}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"


class TestTestVocabularyCorrection:
    """``test_vocabulary_correction``, the \"Test corrections\" panel"""

    def test_happy_path_returns_ack_with_output(self, ipc_server, fake_service):
        fake_service.test_vocabulary_correction.return_value = {
            "input": "teh cat",
            "output": "the cat",
            "applied": True,
        }
        resp = ipc_server._handle_test_vocabulary_correction({"text": "teh cat"}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {
            "input": "teh cat",
            "output": "the cat",
            "applied": True,
        }
        fake_service.test_vocabulary_correction.assert_called_once_with("teh cat")

    def test_missing_text_returns_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_test_vocabulary_correction({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.missing_field"
        fake_service.test_vocabulary_correction.assert_not_called()

    def test_text_too_long_returns_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_test_vocabulary_correction({"text": "x" * 3000}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        fake_service.test_vocabulary_correction.assert_not_called()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.test_vocabulary_correction.side_effect = RuntimeError("boom")
        resp = ipc_server._handle_test_vocabulary_correction({"text": "hi"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
