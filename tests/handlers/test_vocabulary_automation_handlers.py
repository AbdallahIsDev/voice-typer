"""Unit tests for ``VocabularyAutomationHandlersMixin`` (CR-12).

Covers the 3 vocabulary-automation IPC handlers defined in
``voice_typer/server/handlers/vocabulary_automation_handlers.py``:

- ``_handle_get_vocabulary_suggestions`` — returns pending suggestions
  (empty list if automation isn't initialized yet).
- ``_handle_apply_vocabulary_suggestion`` — applies a single suggestion
  (``original`` + ``corrected`` + optional ``timestamp``).
- ``_handle_dismiss_vocabulary_suggestion`` — dismisses a single suggestion.

The apply/dismiss handlers have a richer validation ladder than most:

1. Non-dict ``data`` → ``requires data: object`` error.
2. Automation not initialized (``app._vocabulary_automation is None``)
   → ``vocabulary automation is not initialized`` error.
3. ``original`` / ``corrected`` not strings → type error.
4. No matching pending suggestion → ``suggestion not found in pending list`` error.
5. Success → ``{applied|dismissed: True, original, corrected}`` ack.

The handlers read ``self.app._vocabulary_automation`` directly (not via
the service layer) because the automation instance is lazily created
by the dictation pipeline on the first transcription after the
feature is enabled — there's no service method that owns it.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_suggestion(original="hello", corrected="hi", timestamp=1.0):
    """Build a fake ``CorrectionSuggestion`` for tests.

    The handler matches on ``(original, corrected, timestamp)`` and
    calls ``.to_dict()`` for serialization in the get/list path, and
    ``apply_suggestion()`` / ``dismiss_suggestion()`` on the apply /
    dismiss paths.  A ``MagicMock`` with explicit attributes satisfies
    both call sites without importing the real dataclass.
    """
    s = MagicMock(name=f"Suggestion({original}→{corrected})")
    s.original = original
    s.corrected = corrected
    s.timestamp = timestamp
    s.to_dict.return_value = {
        "original": original,
        "corrected": corrected,
        "timestamp": timestamp,
    }
    return s


class TestGetVocabularySuggestions:
    """``_handle_get_vocabulary_suggestions`` — returns pending suggestions."""

    def test_automation_not_initialized_returns_empty_list(self, ipc_server, fake_app):
        """When ``app._vocabulary_automation`` is None (no dictation yet),
        return ``{suggestions: []}`` rather than raising.

        This lets the Vocabulary page render normally before the user
        has dictated anything.
        """
        # Default fake_app doesn't have _vocabulary_automation set —
        # getattr returns a child MagicMock, not None.  Set it
        # explicitly to None to exercise the "not initialized" path.
        fake_app._vocabulary_automation = None
        resp = ipc_server._handle_get_vocabulary_suggestions({}, {})
        assert resp["type"] == "vocabulary_suggestions"
        assert resp["data"] == {"suggestions": []}

    def test_automation_initialized_returns_pending_suggestions(self, ipc_server, fake_app):
        """When automation exists, return ``[s.to_dict() for s in pending]``."""
        s1 = _make_suggestion("hello", "hi")
        s2 = _make_suggestion("world", "word")
        automation = MagicMock()
        automation.get_pending_suggestions.return_value = [s1, s2]
        fake_app._vocabulary_automation = automation

        resp = ipc_server._handle_get_vocabulary_suggestions({}, {})
        assert resp["type"] == "vocabulary_suggestions"
        assert resp["data"] == {
            "suggestions": [
                {"original": "hello", "corrected": "hi", "timestamp": 1.0},
                {"original": "world", "corrected": "word", "timestamp": 1.0},
            ]
        }
        automation.get_pending_suggestions.assert_called_once_with()

    def test_automation_raises_returns_error(self, ipc_server, fake_app):
        automation = MagicMock()
        automation.get_pending_suggestions.side_effect = RuntimeError("state corrupt")
        fake_app._vocabulary_automation = automation
        resp = ipc_server._handle_get_vocabulary_suggestions({}, {})
        assert resp["type"] == "error"


class TestApplyVocabularySuggestion:
    """``_handle_apply_vocabulary_suggestion`` — applies a single suggestion."""

    def test_non_dict_data_returns_error(self, ipc_server, fake_app):
        resp = ipc_server._handle_apply_vocabulary_suggestion("not-a-dict", {})
        assert resp["type"] == "error"
        assert "data: object" in resp["data"]["message"]

    def test_automation_not_initialized_returns_error(self, ipc_server, fake_app):
        """When automation is None, applying is impossible — return error."""
        fake_app._vocabulary_automation = None
        resp = ipc_server._handle_apply_vocabulary_suggestion({"original": "hello", "corrected": "hi"}, {})
        assert resp["type"] == "error"
        assert "not initialized" in resp["data"]["message"]

    def test_original_not_string_returns_error(self, ipc_server, fake_app):
        """``original`` must be a string — int/list/None are rejected."""
        automation = MagicMock()
        fake_app._vocabulary_automation = automation
        resp = ipc_server._handle_apply_vocabulary_suggestion({"original": 123, "corrected": "hi"}, {})
        assert resp["type"] == "error"
        assert "must be strings" in resp["data"]["message"]
        automation.apply_suggestion.assert_not_called()

    def test_suggestion_not_found_in_pending_returns_error(self, ipc_server, fake_app):
        """If no pending suggestion matches ``(original, corrected, timestamp)``,
        return ``suggestion not found in pending list``.
        """
        automation = MagicMock()
        # Return a suggestion with different values — the lookup won't match.
        automation.get_pending_suggestions.return_value = [
            _make_suggestion(original="different", corrected="different"),
        ]
        fake_app._vocabulary_automation = automation

        resp = ipc_server._handle_apply_vocabulary_suggestion(
            {"original": "hello", "corrected": "hi", "timestamp": 1.0}, {}
        )
        assert resp["type"] == "error"
        assert "not found" in resp["data"]["message"]
        automation.apply_suggestion.assert_not_called()

    def test_happy_path_applies_suggestion_and_returns_ack(self, ipc_server, fake_app):
        """Matching suggestion → ``automation.apply_suggestion(s)`` called,
        ack returned with ``{applied: True, original, corrected}``.
        """
        target = _make_suggestion(original="hello", corrected="hi", timestamp=1.0)
        automation = MagicMock()
        automation.get_pending_suggestions.return_value = [target]
        fake_app._vocabulary_automation = automation

        resp = ipc_server._handle_apply_vocabulary_suggestion(
            {"original": "hello", "corrected": "hi", "timestamp": 1.0}, {}
        )
        assert resp["type"] == "ack"
        assert resp["data"] == {
            "applied": True,
            "original": "hello",
            "corrected": "hi",
        }
        automation.apply_suggestion.assert_called_once_with(target)

    def test_matching_without_timestamp(self, ipc_server, fake_app):
        """If ``timestamp`` is omitted from the payload, the matcher
        skips the timestamp comparison (matches on original+corrected only).
        """
        target = _make_suggestion(original="hello", corrected="hi", timestamp=1.0)
        automation = MagicMock()
        automation.get_pending_suggestions.return_value = [target]
        fake_app._vocabulary_automation = automation

        resp = ipc_server._handle_apply_vocabulary_suggestion(
            {"original": "hello", "corrected": "hi"},
            {},  # no timestamp
        )
        assert resp["type"] == "ack"
        automation.apply_suggestion.assert_called_once_with(target)


class TestDismissVocabularySuggestion:
    """``_handle_dismiss_vocabulary_suggestion`` — dismisses a single suggestion."""

    def test_non_dict_data_returns_error(self, ipc_server, fake_app):
        resp = ipc_server._handle_dismiss_vocabulary_suggestion(None, {})
        assert resp["type"] == "error"
        assert "data: object" in resp["data"]["message"]

    def test_automation_not_initialized_returns_error(self, ipc_server, fake_app):
        fake_app._vocabulary_automation = None
        resp = ipc_server._handle_dismiss_vocabulary_suggestion({"original": "hello", "corrected": "hi"}, {})
        assert resp["type"] == "error"
        assert "not initialized" in resp["data"]["message"]

    def test_suggestion_not_found_returns_error(self, ipc_server, fake_app):
        automation = MagicMock()
        automation.get_pending_suggestions.return_value = []
        fake_app._vocabulary_automation = automation
        resp = ipc_server._handle_dismiss_vocabulary_suggestion({"original": "hello", "corrected": "hi"}, {})
        assert resp["type"] == "error"
        assert "not found" in resp["data"]["message"]

    def test_happy_path_dismisses_and_returns_ack(self, ipc_server, fake_app):
        target = _make_suggestion(original="hello", corrected="hi", timestamp=2.0)
        automation = MagicMock()
        automation.get_pending_suggestions.return_value = [target]
        fake_app._vocabulary_automation = automation

        resp = ipc_server._handle_dismiss_vocabulary_suggestion(
            {"original": "hello", "corrected": "hi", "timestamp": 2.0}, {}
        )
        assert resp["type"] == "ack"
        assert resp["data"] == {
            "dismissed": True,
            "original": "hello",
            "corrected": "hi",
        }
        automation.dismiss_suggestion.assert_called_once_with(target)

    def test_original_or_corrected_missing_returns_error(self, ipc_server, fake_app):
        """Missing ``original`` → ``original`` is None → not a string → error."""
        automation = MagicMock()
        fake_app._vocabulary_automation = automation
        resp = ipc_server._handle_dismiss_vocabulary_suggestion(
            {"corrected": "hi"},
            {},  # no original
        )
        assert resp["type"] == "error"
        assert "must be strings" in resp["data"]["message"]
        automation.dismiss_suggestion.assert_not_called()
