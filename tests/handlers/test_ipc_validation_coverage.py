"""IPC-3: coverage tests for the 10 newly-validated handlers.

Each handler in this file was missing ``_validate_dict_payload``
coverage before the IPC-3 fix.  The existing tests in
``tests/handlers/test_*.py`` cover the *happy path* and the
*missing-field* path (where applicable) for these handlers, but they
do NOT cover the new *invalid-type* path that ``_validate_dict_payload``
adds.  This file fills that gap.

Layout
------

One test class per handler, each with at least:

* ``test_invalid_field_type_returns_invalid_field_error`` — a field
  that is present but has the wrong type (e.g. ``{"model": 123}``)
  must return ``{"type": "error", "data": {"code": "invalid_field",
  "field": <name>, ...}}``.
* ``test_non_dict_payload_returns_invalid_payload_error`` (where
  applicable) — a non-dict ``data`` payload must return
  ``{"type": "error", "data": {"code": "invalid_payload", ...}}``.

The 10 handlers covered:

1. ``_handle_download_model`` (model_handlers)
2. ``_handle_delete_model`` (model_handlers)
3. ``_handle_import_model`` (model_handlers)
4. ``_handle_microphone_test_start`` (microphone_test_handlers)
5. ``_handle_level_monitor_start`` (level_monitor_handlers)
6. ``_handle_set_esc_cancel_paused`` (system_handlers)
7. ``_handle_get_history`` (history_handlers)
8. ``_handle_get_favorites`` (history_handlers)
9. ``_handle_search_history`` (history_handlers)
10. ``_handle_toggle_dictation`` (dictation_handlers)

The remaining 50+ handlers either (a) already had
``_validate_dict_payload`` (8 handlers: ``save_templates``,
``set_tray_locale``, ``delete_history``, ``restore_history``,
``toggle_favorite``, ``onboarding_set_microphone``,
``onboarding_set_hotkey``, ``onboarding_set_model``), (b) use a
domain-specific validator that is MORE rigorous than
``_validate_dict_payload`` (``set_config`` via
``validate_config_update``; ``save_vocabulary`` via the inline
1 MiB / 1024-char caps; ``show_electron_notification`` via the
4-field per-type check; ``apply_vocabulary_suggestion`` /
``dismiss_vocabulary_suggestion`` via the
``original`` + ``corrected`` string check), or (c) are no-field poll
handlers (``get_status``, ``get_rms_level``, ``microphone_test_status``,
etc.) where adding the trivial empty-schema validation is left as
follow-up work — it would tighten the contract (rejecting non-dict
``data`` that is currently silently ignored) but does not add field
validation because there are no fields to validate.
"""

from __future__ import annotations

import logging
import os

import pytest


class TestDownloadModelValidation:
    """``_handle_download_model`` — IPC-3 invalid-type coverage."""

    def test_non_string_model_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{"model": 123}`` → ``code: invalid_field, field: model``."""
        resp = ipc_server._handle_download_model({"model": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "model"
        fake_service.download_model.assert_not_called()

    def test_list_model_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{"model": ["small.en"]}`` → ``code: invalid_field``."""
        resp = ipc_server._handle_download_model({"model": ["small.en"]}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "model"
        fake_service.download_model.assert_not_called()

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        """``"not-a-dict"`` → ``code: invalid_payload``.

        Pre-IPC-3, a non-dict payload was silently coerced to an empty
        string via ``(data or {}).get("model", "") if isinstance(data,
        dict) else ""``, hitting the inline "Missing 'model' parameter"
        branch. Post-IPC-3, ``_validate_dict_payload`` rejects the
        non-dict with a structured ``invalid_payload`` error before the
        inline check runs.
        """
        resp = ipc_server._handle_download_model("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.download_model.assert_not_called()


# ── 2. delete_model ──────────────────────────────────────────────────────


class TestDeleteModelValidation:
    """``_handle_delete_model`` — IPC-3 invalid-type coverage."""

    def test_non_string_model_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_delete_model({"model": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "model"
        fake_service.delete_model.assert_not_called()

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_delete_model(None, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.delete_model.assert_not_called()


# ── 3. import_model ──────────────────────────────────────────────────────


class TestImportModelValidation:
    """``_handle_import_model`` — IPC-3 invalid-type coverage."""

    def test_non_string_dir_path_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{"dir_path": 123}`` → ``code: invalid_field, field: dir_path``.

        Pre-IPC-3, the inline ``isinstance(dir_path, str)`` check
        caught this with a generic "Missing 'dir_path' parameter"
        message (the same message used for the missing-field case).
        Post-IPC-3, ``_validate_dict_payload`` returns the structured
        ``invalid_field`` code with the field name, so the client can
        distinguish "missing" from "wrong type".
        """
        resp = ipc_server._handle_import_model({"dir_path": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "dir_path"
        fake_service.import_model.assert_not_called()

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_import_model(["not", "a", "dict"], {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.import_model.assert_not_called()


# ── 4. microphone_test_start ────────────────────────────────────────────


class TestMicrophoneTestStartValidation:
    """``_handle_microphone_test_start`` — IPC-3 invalid-type coverage."""

    def test_non_string_mic_id_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{"mic_id": 123}`` → ``code: invalid_field, field: mic_id``."""
        resp = ipc_server._handle_microphone_test_start({"mic_id": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "mic_id"
        fake_service.microphone_test_start.assert_not_called()

    def test_non_dict_filters_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{"filters": "not-a-dict"}`` → ``code: invalid_field, field: filters``.

        ``filters`` is the ADR 0007 filter-config DICT (the renderer's
        ``buildTestFilters`` output); any non-dict, non-None value is
        rejected at the boundary because every downstream consumer of
        the value requires a mapping.
        """
        resp = ipc_server._handle_microphone_test_start({"filters": "not-a-dict"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "filters"
        fake_service.microphone_test_start.assert_not_called()

    def test_none_mic_id_is_accepted(self, ipc_server, fake_service):
        """``{"mic_id": None}`` → accepted (None is in the allowed type tuple).

        The schema declares ``type: (str, type(None))`` so an explicit
        JSON null (Python ``None``) is a valid value — it means "use
        the default microphone".  This preserves the pre-IPC-3 behavior
        where ``d.get("mic_id", None)`` returned None for both absent
        and explicit-null cases.
        """
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"mic_id": None}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=10.0,
            filters=None,
        )


# ── 5. level_monitor_start ──────────────────────────────────────────────


class TestLevelMonitorStartValidation:
    """``_handle_level_monitor_start`` — IPC-3 invalid-type coverage."""

    def test_non_string_mic_id_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_level_monitor_start({"mic_id": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "mic_id"
        fake_service.level_monitor_start.assert_not_called()

    def test_non_dict_payload_pre_coerced_to_defaults(self, ipc_server, fake_service):
        """Non-dict ``data`` is pre-coerced to ``{}`` so the existing
        "non-dict → mic_id=None default" contract still holds.  The
        pre-coercion is intentional: ``_validate_dict_payload`` would
        otherwise reject the non-dict with ``invalid_payload``, breaking
        the documented backward-compat behavior in
        ``test_non_dict_data_defaults_mic_id_to_none``.
        """
        fake_service.level_monitor_start.return_value = {"running": True}
        resp = ipc_server._handle_level_monitor_start(None, {})
        assert resp["type"] == "level_monitor_status"
        fake_service.level_monitor_start.assert_called_once_with(mic_id=None)


# ── 6. set_esc_cancel_paused ────────────────────────────────────────────


class TestSetEscCancelPausedValidation:
    """``_handle_set_esc_cancel_paused`` — IPC-3 invalid-type coverage."""

    def test_non_bool_paused_returns_invalid_field_error(self, ipc_server, fake_app):
        """``{"paused": "true"}`` → ``code: invalid_field, field: paused``.

        Pre-IPC-3, the inline ``bool((data or {}).get("paused",
        False))`` coercion would silently accept the string "true"
        as ``True`` (any non-empty string is truthy), potentially
        escalating the ESC-cancel pause state when the caller intended
        to resume.  Post-IPC-3, the strict ``bool`` type check rejects
        the string with a structured ``invalid_field`` error.
        """
        resp = ipc_server._handle_set_esc_cancel_paused({"paused": "true"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "paused"

    def test_int_paused_returns_invalid_field_error(self, ipc_server, fake_app):
        """``{"paused": 1}`` → ``code: invalid_field, field: paused``.

        ``bool`` is a subclass of ``int`` in Python, but
        ``isinstance(1, bool)`` is ``False`` — so the strict ``bool``
        check rejects ``1`` (which the previous ``bool(1)`` coercion
        would have accepted as ``True``).
        """
        resp = ipc_server._handle_set_esc_cancel_paused({"paused": 1}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "paused"

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server, fake_app):
        resp = ipc_server._handle_set_esc_cancel_paused("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"


# ── 7. get_history ───────────────────────────────────────────────────────


class TestGetHistoryValidation:
    """``_handle_get_history`` — IPC-3 invalid-type coverage."""

    def test_list_limit_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{"limit": [50]}`` → ``code: invalid_field, field: limit``.

        Pre-IPC-3, a list ``limit`` was silently passed to
        ``_bound_history_limit`` which fell through to the default
        (50) via the ``int(raw)`` TypeError fall-through.  Post-IPC-3,
        the ``(int, str)`` type check rejects the list with a
        structured ``invalid_field`` error.

        Note: ``int`` and ``str`` are both accepted (the schema is
        ``(int, str)``) because the renderer sometimes sends numeric
        strings from form inputs (see
        ``test_get_history_with_string_limit_accepted`` in
        ``tests/test_server.py``); ``_bound_history_limit`` does the
        actual ``int()`` coercion.
        """
        resp = ipc_server._handle_get_history({"limit": [50]}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "limit"
        fake_service.get_history.assert_not_called()

    def test_dict_offset_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{"offset": {"x": 0}}`` → ``code: invalid_field, field: offset``."""
        resp = ipc_server._handle_get_history({"offset": {"x": 0}}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "offset"

    def test_bool_limit_accepted_due_to_int_subclass(self, ipc_server, fake_service):
        """``bool`` is a subclass of ``int`` — ``isinstance(True, int)``
        is ``True``, so a bool passes the ``(int, str)`` type check.
        We document this as a known gap (the inline
        ``_bound_history_limit`` helper clamps it to 1 or 0) rather
        than adding a ``bool`` exclusion to ``_validate_dict_payload``
        — that would be a behavior change for the 8 already-validated
        handlers too.
        """
        # bool sneaks through because isinstance(True, int) is True.
        # Document the gap: the call succeeds (no invalid_field error).
        resp = ipc_server._handle_get_history({"limit": True}, {})
        # The handler clamps True → 1 via _bound_history_limit, so the
        # call succeeds.  Asserting this behavior pins it so a future
        # tightening (e.g. excluding bool in _validate_dict_payload)
        # would surface here as a deliberate contract change.
        assert resp["type"] == "history"
        fake_service.get_history.assert_called_once()

    def test_string_numeric_limit_accepted(self, ipc_server, fake_service):
        """``{"limit": "25"}`` → accepted (numeric string is coerced
        by ``_bound_history_limit``).

        This preserves the existing
        ``test_get_history_with_string_limit_accepted`` contract in
        ``tests/test_server.py``: the renderer sometimes sends numeric
        strings from form inputs, and the ``(int, str)`` schema
        accepts them so the inline coercion can run.
        """
        resp = ipc_server._handle_get_history({"limit": "25"}, {})
        assert resp["type"] == "history"
        fake_service.get_history.assert_called_once_with(25, 0)

    def test_non_dict_payload_pre_coerced_to_defaults(self, ipc_server, fake_service):
        """Non-dict ``data`` is pre-coerced to ``{}`` so the existing
        ``test_non_dict_data_falls_back_to_defaults`` contract still
        holds (list → defaults 50/0).
        """
        fake_service.get_history.return_value = []
        resp = ipc_server._handle_get_history(["not", "a", "dict"], {})
        assert resp["type"] == "history"
        fake_service.get_history.assert_called_once_with(50, 0)


# ── 8. get_favorites ────────────────────────────────────────────────────


class TestGetFavoritesValidation:
    """``_handle_get_favorites`` — IPC-3 invalid-type coverage."""

    def test_list_limit_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_get_favorites({"limit": [50]}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "limit"
        fake_service.get_favorites.assert_not_called()

    def test_dict_offset_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_get_favorites({"offset": {"x": 0}}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "offset"


# ── 9. search_history ───────────────────────────────────────────────────


class TestSearchHistoryValidation:
    """``_handle_search_history`` — IPC-3 invalid-type coverage."""

    def test_non_string_query_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{"query": 123}`` → ``code: invalid_field, field: query``."""
        resp = ipc_server._handle_search_history({"query": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "query"
        fake_service.search_history.assert_not_called()

    def test_list_limit_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_search_history({"limit": [50]}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "limit"

    def test_non_dict_payload_pre_coerced_to_defaults(self, ipc_server, fake_service):
        """Non-dict ``data`` is pre-coerced to ``{}`` so the existing
        ``test_non_dict_data_uses_empty_query`` contract still holds
        (None → empty query, default limit/offset).
        """
        fake_service.search_history.return_value = []
        resp = ipc_server._handle_search_history(None, {})
        assert resp["type"] == "history"
        fake_service.search_history.assert_called_once_with("", 50, 0)


# ── 10. toggle_dictation ────────────────────────────────────────────────


class TestToggleDictationValidation:
    """``_handle_toggle_dictation`` — IPC-3 invalid-payload coverage.

    ``toggle_dictation`` reads no fields from ``data``, so this is a
    contract-tightening validation: a non-dict ``data`` payload (e.g.
    ``{"data": "not-a-dict"}`` — a protocol violation of the
    ``{"type":<cmd>,"data":{...}}`` envelope) is now rejected with
    ``invalid_payload`` rather than silently accepted.

    Note: ``None`` (the value ``msg.get("data")`` returns when the
    ``data`` key is absent, as in ``{"id": 1, "type":
    "toggle_dictation"}``) is pre-coerced to ``{}`` so the validation
    passes cleanly — every existing caller that omits ``data`` still
    gets an ``ack``.
    """

    def test_non_dict_string_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        """A non-None non-dict payload (e.g. a string) is rejected."""
        resp = ipc_server._handle_toggle_dictation("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.toggle_dictation.assert_not_called()

    def test_non_dict_list_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        """A list payload is rejected."""
        resp = ipc_server._handle_toggle_dictation(["not", "a", "dict"], {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.toggle_dictation.assert_not_called()

    def test_none_payload_pre_coerced_to_empty_dict(self, ipc_server, fake_service):
        """``None`` (the value when the ``data`` key is absent) is
        pre-coerced to ``{}`` so the validation passes cleanly.

        This preserves the contract that ``{"id": 1, "type":
        "toggle_dictation"}`` (no ``data`` key) returns ``ack`` —
        every existing Electron caller and test that omits ``data``
        depends on this.
        """
        resp = ipc_server._handle_toggle_dictation(None, {})
        assert resp["type"] == "ack"
        fake_service.toggle_dictation.assert_called_once_with()

    def test_empty_dict_still_works(self, ipc_server, fake_service):
        """Regression: the happy path ``{}`` must still validate cleanly
        and reach ``service.toggle_dictation()``.
        """
        resp = ipc_server._handle_toggle_dictation({}, {})
        assert resp["type"] == "ack"
        fake_service.toggle_dictation.assert_called_once_with()


# ==============================================================================
# Merged from tests/test_handler_group_b_fixes.py —
#   handlers group-B hardening regression pins (traceback scrubbing, control-char rejection, get_status validation
#   wrap, restore-history payload caps, mic-test duration clamp, fixed-string no-echo contracts)
# ==============================================================================
# DE-2H (session-DE): regression tests for the Group 4 findings fixed
# in handlers group B.
#
# Covers six findings from the comprehensive Group 4 review:
#
# - **DE-38** — ``_base.py: _respond_with_error`` logs the full
# traceback to ``voice-typer.log``, which ``export_diagnostics``
# ships back to the renderer. Tracebacks embed absolute file paths
# (which contain the username) and may carry API-key fragments. The
# fix adds :func:`_scrub_traceback` which strips home-directory path
# components and known secret patterns (``sk-``, ``gsk_``,
# ``Bearer ...``, 20+ char bare tokens) from both ``str(exc)`` and
# the formatted traceback BEFORE they land in the log.
#
# - **DE-42** — ``system_handlers: _handle_show_electron_notification``
# enforces ``max_value_len`` on ``title`` / ``message`` but performs
# no control-character sanitization. The fix rejects any char in the
# Unicode ``Cc`` / ``Cf`` categories except ``\\t`` (ANSI escapes,
# terminal bell, newline / CR, RTL overrides, zero-width marks, BOM).
#
# - **DE-43** — ``status_handlers: _handle_get_status`` was the only
# handler in the slice with NO ``try/except`` and NO
# ``_validate_dict_payload`` call. The fix wraps the body in a
# ``try/except Exception`` routing through
# :meth:`HandlerBase._respond_with_error`, and prepends a
# ``_validate_dict_payload(data, {})`` call so a non-dict payload is
# rejected with ``invalid_payload``.
#
# - **DE-44** — ``history_handlers: _handle_restore_history`` had no
# ``max_payload_bytes`` cap and no per-field cap on
# ``record['text']``. The fix adds a 256 KB whole-payload cap plus
# an inline 8192-char per-field cap on ``record['text']``.
#
# - **DE-45** — ``microphone_test_handlers: _handle_microphone_test_start``
# had no upper / lower bound on ``duration``. The fix adds
# ``clamp_range: (1.0, 60.0)`` to the schema, preserving the
# documented string → float coercion (``"7.5" → 7.5``).
#
# - **DE-46** — ``status_handlers: _handle_run_prewarm`` /
# ``_handle_open_prewarm_log`` echoed ``str(e)`` back to the
# renderer in 4 specific-exception branches, leaking the username
# via the embedded absolute path on Windows / macOS. The fix
# replaces the 4 ``f'...: {e}'`` messages with fixed strings; the
# full ``str(e)`` is still logged server-side at ERROR.
#
# (Wave 3, 2026-08-14): the ``_handle_run_prewarm`` /
# ``_handle_open_prewarm_log`` handlers were REMOVED entirely —
# prewarm became a worker startup phase (master plan §6.2 P-1), so
# the slim core no longer spawns a separate prewarm process or opens
# a dedicated prewarm log. The ``TestRunPrewarmNoStrEcho`` and
# ``TestOpenPrewarmLogNoStrEcho`` classes (4 tests) were deleted in
# lockstep, along with the ``test_run_prewarm_oserror_still_returns_error_envelope``
# regression-guard in ``TestExistingContractsPreserved``. The DE-46
# fixed-string-no-echo invariant itself is still pinned by the
# surviving ``TestNoStrEcho`` suite (other handlers in the slice
# that have specific-exception branches with the same fixed-string
# pattern).
#
# (2026-08-14, later the same day): ``_handle_open_prewarm_log`` was
# RESTORED verbatim from 5a319872 along with ``_handle_get_prewarm_status``
# (plan §6.3 addendum — Settings → About Cache Status card); it
# opens ``worker.log`` instead of the retired ``prewarm.log`` and
# keeps the DE-46 fixed-string-no-echo invariant. ``_handle_run_prewarm``
# was ALSO restored (addendum 2nd half) but RE-IMPLEMENTED: instead
# of spawning the deleted standalone-prewarm subprocess it re-runs
# the worker's warm phase in-process via
# ``prewarm.status.run_prewarm_now()`` (warm_imports_for_worker on a
# daemon thread + status-file refresh). The DE-46 fixed-string-no-echo
# invariant still holds — the handler routes exceptions through
# ``_respond_with_error`` / ``_error_response`` with fixed strings.
#


class TestScrubTraceback:
    """DE-38: ``_scrub_traceback`` strips secrets + home-dir paths."""

    def test_scrubs_openai_style_api_key_from_exception_message(self):
        """A ``sk-...`` key embedded in ``str(exc)`` is replaced."""
        from voice_typer.server.handlers._base import _scrub_traceback

        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        exc = RuntimeError(f"key not found: {secret}")
        scrubbed_str, _ = _scrub_traceback(exc)
        assert secret not in scrubbed_str, f"API key leaked through scrub: {scrubbed_str!r}"
        # The redaction marker should be present (the canonical
        # ``redact_secret`` helper substitutes ``***`` for bare keys).
        assert "***" in scrubbed_str or "[REDACTED]" in scrubbed_str, (
            f"Expected a redaction marker, got: {scrubbed_str!r}"
        )

    def test_scrubs_groq_style_api_key_from_exception_message(self):
        """A ``gsk_...`` key embedded in ``str(exc)`` is replaced."""
        from voice_typer.server.handlers._base import _scrub_traceback

        secret = "gsk_" + "a" * 30
        exc = RuntimeError(f"invalid Groq key: {secret}")
        scrubbed_str, _ = _scrub_traceback(exc)
        assert secret not in scrubbed_str, f"Groq API key leaked through scrub: {scrubbed_str!r}"

    def test_scrubs_bearer_token_from_exception_message(self):
        """A ``Bearer <token>`` string in ``str(exc)`` is redacted."""
        from voice_typer.server.handlers._base import _scrub_traceback

        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        exc = RuntimeError(f"Authorization: Bearer {secret} rejected")
        scrubbed_str, _ = _scrub_traceback(exc)
        assert secret not in scrubbed_str, f"Bearer token leaked through scrub: {scrubbed_str!r}"

    def test_scrubs_home_directory_path_from_exception_message(self):
        """Home-directory path components are replaced with ``~``."""
        from voice_typer.server.handlers._base import _scrub_traceback

        home = os.path.expanduser("~")
        if home in ("/", "~", ""):
            pytest.skip("HOME is not set or is root — cannot test home-dir scrub")
        exc = RuntimeError(f"failed to open {home}/.config/voice-typer/config.json")
        scrubbed_str, _ = _scrub_traceback(exc)
        assert home not in scrubbed_str, f"Home directory leaked through scrub: {scrubbed_str!r}"

    def test_scrubs_home_directory_path_from_traceback_text(self):
        """Home-directory paths in the formatted traceback are scrubbed."""
        from voice_typer.server.handlers._base import _scrub_traceback

        home = os.path.expanduser("~")
        if home in ("/", "~", ""):
            pytest.skip("HOME is not set or is root — cannot test home-dir scrub")
        try:
            # Raise an exception whose traceback frames will include
            # the home-dir path (via the file path of this test).
            raise RuntimeError(f"failed at {home}/.cache/model.bin")
        except RuntimeError as exc:
            _, scrubbed_tb = _scrub_traceback(exc)
        assert home not in scrubbed_tb, f"Home directory leaked through traceback scrub: {scrubbed_tb!r}"

    def test_respond_with_error_does_not_leak_secret_to_response(
        self,
    ):
        """The renderer-facing response envelope never carries the secret.

        DE-38's primary guarantee is unchanged by this fix (the
        envelope was always ``{"code": "server.internal_error",
        "message": "internal error"}``) — but we assert it here as a
        regression guard so a future careless change can't reintroduce
        the ``str(exc)`` leak in the response.
        """
        from voice_typer.server.handlers._base import HandlerBase

        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        helper = HandlerBase()
        resp: dict = {"id": 1}
        result = helper._respond_with_error(resp, RuntimeError(f"key not found: {secret}"), "test_cmd")
        assert secret not in str(result), f"Secret leaked into response envelope: {result!r}"
        assert result["data"]["message"] == "internal error"

    def test_respond_with_error_scrubs_secret_from_log(self, ipc_server, fake_service, caplog):
        """DE-38: the log record's formatted message must not carry the secret.

        The ``voice-typer.log`` file is shipped to the renderer when the
        user attaches a diagnostics bundle to a bug report (the export
        path is now in the Tauri Rust host — see UE-15), so any secret
        that reaches the log is exfiltrated. This test asserts the
        scrubbed log message redacts the ``sk-...`` key. ``record.exc_info``
        is still set so structured-logging consumers and existing
        ``r.exc_info is not None`` test assertions continue to hold.

        UE-15 (2026-07-30): was ``_handle_export_diagnostics`` (deleted
        from ``SystemHandlersMixin``); switched to
        ``_handle_cancel_model_download`` (a sibling handler with the
        same catch-all path).
        """
        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        fake_service.cancel_model_download.side_effect = RuntimeError(f"key not found: {secret}")
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.ipc_server"):
            resp = ipc_server._handle_cancel_model_download({}, {})

        # The renderer envelope is unchanged.
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"

        # The log record's formatted message must not carry the secret.
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, "catch-all must log at ERROR level"
        for record in error_records:
            formatted = record.getMessage()
            assert secret not in formatted, f"Secret leaked into log message: {formatted!r}"
            # If a traceback was attached (record.exc_text or via
            # exc_info formatting), scrub it too.
            if record.exc_text:
                assert secret not in record.exc_text, f"Secret leaked into record.exc_text: {record.exc_text!r}"
        # ``record.exc_info`` must still be set so structured-logging
        # consumers and the existing
        # ``test_catch_all_logs_at_error_level_with_exc_info``
        # assertion continue to hold.
        assert any(r.exc_info is not None for r in error_records), (
            "DE-38 scrub must preserve record.exc_info (test_catch_all_logs_at_"
            "error_level_with_exc_info asserts it is not None)."
        )

    def test_respond_with_error_scrubs_home_dir_from_log(self, ipc_server, fake_service, caplog, monkeypatch):
        """DE-38: home-directory paths in the log are replaced with ``~``.

        UE-15 (2026-07-30): was ``_handle_export_diagnostics`` (deleted
        from ``SystemHandlersMixin``); switched to
        ``_handle_cancel_model_download`` (a sibling handler with the
        same catch-all path).
        """
        home = os.path.expanduser("~")
        if home in ("/", "~", ""):
            pytest.skip("HOME is not set or is root — cannot test home-dir scrub")
        fake_service.cancel_model_download.side_effect = RuntimeError(
            f"failed to open {home}/.config/voice-typer/config.json"
        )
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.ipc_server"):
            ipc_server._handle_cancel_model_download({}, {})

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records
        for record in error_records:
            formatted = record.getMessage()
            assert home not in formatted, f"Home dir leaked into log message: {formatted!r}"


# ────────────────────────────────────────────────────────────────────────────
# control-char rejection in _handle_show_electron_notification
# ────────────────────────────────────────────────────────────────────────────


class TestControlCharRejection:
    """DE-42: ``title`` / ``message`` reject Cc/Cf chars except ``\\t``."""

    def test_ansi_escape_in_title_is_rejected(self, ipc_server):
        """An ANSI color escape (``\\x1b[31m``) in ``title`` → invalid_field."""
        resp = ipc_server._handle_show_electron_notification(
            {"title": "\x1b[31mRed Title\x1b[0m", "message": "ok"},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "title"

    def test_newline_in_title_is_rejected(self, ipc_server):
        """A newline (``\\n``) in ``title`` → invalid_field."""
        resp = ipc_server._handle_show_electron_notification(
            {"title": "line1\nline2", "message": "ok"},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "title"

    def test_terminal_bell_in_message_is_rejected(self, ipc_server):
        """A terminal bell (``\\x07``) in ``message`` → invalid_field."""
        resp = ipc_server._handle_show_electron_notification(
            {"title": "ok", "message": "beep\x07beep"},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "message"

    def test_rtl_override_in_title_is_rejected(self, ipc_server):
        """A Unicode RTL override (``\\u202e``) in ``title`` → invalid_field.

        RTL overrides can spoof a critical notification by reversing
        the displayed character order (e.g. ``"\u202e" + "1 step" +
        " until deletion"`` may render as "noitaced un..." backwards).
        The Cf category catches this.
        """
        resp = ipc_server._handle_show_electron_notification(
            {"title": "alert\u202eevah", "message": "ok"},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "title"

    def test_zero_width_joiner_in_message_is_rejected(self, ipc_server):
        """A ZWJ (``\\u200d``) in ``message`` → invalid_field (Cf category)."""
        resp = ipc_server._handle_show_electron_notification(
            {"title": "ok", "message": "a\u200db"},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "message"

    def test_bom_in_title_is_rejected(self, ipc_server):
        """A Byte Order Mark (``\\ufeff``) in ``title`` → invalid_field."""
        resp = ipc_server._handle_show_electron_notification(
            {"title": "\ufeffHello", "message": "ok"},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "title"

    def test_tab_in_message_is_accepted(self, ipc_server):
        """A horizontal tab (``\\t``) in ``message`` is accepted.

        ``\\t`` is in the ``Cc`` category but is explicitly allowed
        (tabular layout in the message body is common and harmless;
        the OS notification APIs render it consistently as whitespace).
        """
        captured: list[dict] = []
        from voice_typer.server import event_bus

        sub = captured.append
        event_bus.subscribe(sub)
        try:
            resp = ipc_server._handle_show_electron_notification(
                {"title": "ok", "message": "col1\tcol2"},
                {},
            )
        finally:
            event_bus.unsubscribe(sub)
        assert resp["type"] == "ack", f"Tab should be accepted, got error: {resp}"
        assert captured[0]["data"]["message"] == "col1\tcol2"

    def test_clean_payload_is_accepted(self, ipc_server):
        """A payload with no control chars is accepted (sanity check)."""
        captured: list[dict] = []
        from voice_typer.server import event_bus

        sub = captured.append
        event_bus.subscribe(sub)
        try:
            resp = ipc_server._handle_show_electron_notification(
                {"title": "Hello World", "message": "Just a normal message."},
                {},
            )
        finally:
            event_bus.unsubscribe(sub)
        assert resp["type"] == "ack"
        assert captured[0]["data"]["title"] == "Hello World"


# ────────────────────────────────────────────────────────────────────────────
# _handle_get_status try/except + payload validation
# ────────────────────────────────────────────────────────────────────────────


class TestGetStatusValidation:
    """DE-43: ``_handle_get_status`` validates payload + catches exceptions."""

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        """A non-dict ``data`` (list) → ``code: invalid_payload``.

        Before DE-43, this was the only status handler that silently
        accepted a non-dict payload — every sibling handler rejected
        it. The fix aligns ``get_status`` with the documented
        ADR-0020 §2 contract.
        """
        resp = ipc_server._handle_get_status(["not", "a", "dict"], {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.get_status.assert_not_called()

    def test_string_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        """A string ``data`` → ``code: invalid_payload``."""
        resp = ipc_server._handle_get_status("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"

    def test_none_payload_is_coerced_to_empty_dict(self, ipc_server, fake_service):
        """``None`` is pre-coerced to ``{}`` (matches the toggle_dictation pattern)."""
        fake_service.get_status.return_value = {"status": "idle"}
        resp = ipc_server._handle_get_status(None, {})
        assert resp["type"] == "status"
        assert resp["data"] == {"status": "idle"}

    def test_service_raises_returns_internal_error_envelope(self, ipc_server, fake_service):
        """``service.get_status()`` raising → catch-all envelope.

        Before DE-43, the exception propagated to the dispatcher's
        outer catch-all, losing the ``cmd_name='get_status'`` log
        attribution. The fix routes through
        ``_respond_with_error(resp, exc, 'get_status')``.
        """
        fake_service.get_status.side_effect = RuntimeError("recorder not started")
        resp = ipc_server._handle_get_status({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


# ────────────────────────────────────────────────────────────────────────────
# restore_history payload + per-field caps
# ────────────────────────────────────────────────────────────────────────────


class TestRestoreHistoryPayloadCap:
    """DE-44: ``restore_history`` caps payload size + ``record['text']`` length."""

    def test_oversized_text_field_returns_payload_too_large(self, ipc_server, fake_service):
        """``record['text']`` > 8192 chars → ``code: payload_too_large``."""
        oversized_text = "x" * 10_000  # > 8192-char cap
        record = {"id": 1, "text": oversized_text}
        resp = ipc_server._handle_restore_history({"record": record}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.payload_too_large"
        assert resp["data"]["field"] == "record.text"
        fake_service.restore_history.assert_not_called()

    def test_oversized_whole_payload_returns_invalid_payload(self, ipc_server, fake_service):
        """A >256 KB serialized payload → ``code: invalid_payload``.

        The 256 KB whole-payload cap (``max_payload_bytes``) catches a
        malicious caller who stuffs a giant blob into a non-``text``
        field. Without this guard, the per-field ``text`` cap alone
        wouldn't catch the bloat.
        """
        # Build a payload just over 256 KB without putting the bulk in
        # ``record['text']`` (which would trip the per-field cap first).
        # 256 KB = 262144 bytes. A JSON-stringified payload of a single
        # 300_000-char string in ``record['blob']`` will comfortably
        # exceed the cap.
        giant_blob = "y" * 300_000
        record = {"id": 1, "text": "ok", "blob": giant_blob}
        resp = ipc_server._handle_restore_history({"record": record}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.restore_history.assert_not_called()

    def test_text_at_exactly_8192_chars_is_accepted(self, ipc_server, fake_service):
        """``record['text']`` of exactly 8192 chars is on the boundary — accepted."""
        fake_service.restore_history.return_value = 42
        boundary_text = "x" * 8192
        record = {"id": 1, "text": boundary_text}
        resp = ipc_server._handle_restore_history({"record": record}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"id": 42}
        fake_service.restore_history.assert_called_once_with(record)

    def test_normal_payload_is_accepted(self, ipc_server, fake_service):
        """A normal-sized record is accepted (regression check)."""
        fake_service.restore_history.return_value = 7
        record = {"id": 1, "text": "restored transcription"}
        resp = ipc_server._handle_restore_history({"record": record}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"id": 7}


# ────────────────────────────────────────────────────────────────────────────
# microphone_test_start duration clamp_range
# ────────────────────────────────────────────────────────────────────────────


class TestDurationClampRange:
    """DE-45: ``duration`` is clamped to ``[1.0, 60.0]``."""

    def test_huge_numeric_duration_is_clamped_to_60(self, ipc_server, fake_service):
        """``duration=1e300`` → clamped to 60.0 (DoS guard)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": 1e300}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=60.0,
            filters=None,
        )

    def test_negative_numeric_duration_is_clamped_to_1(self, ipc_server, fake_service):
        """``duration=-5.0`` → clamped to 1.0 (lower bound)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": -5.0}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=1.0,
            filters=None,
        )

    def test_huge_string_duration_is_clamped_to_60(self, ipc_server, fake_service):
        """``duration="1e300"`` (string) → coerced + clamped to 60.0.

        The string-coercion path (documented for form-input
        compatibility) must apply the same clamp as the numeric path.
        """
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": "1e300"}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=60.0,
            filters=None,
        )

    def test_zero_duration_is_clamped_to_1(self, ipc_server, fake_service):
        """``duration=0`` → clamped to 1.0 (lower bound).

        Note: the previous impl's ``float(d.get("duration") or 10.0)``
        would have treated ``0`` as falsy and used the default 10.0.
        The new clamp treats ``0`` as a real value and clamps it to
        the lower bound 1.0. This is the documented behavior change
        in DE-45 — ``0`` is no longer "use default", it's a clamped
        value.
        """
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": 0}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=1.0,
            filters=None,
        )

    def test_in_bounds_numeric_duration_passes_through(self, ipc_server, fake_service):
        """``duration=7.5`` → 7.5 (no clamping needed)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": 7.5}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=7.5,
            filters=None,
        )

    def test_in_bounds_string_duration_is_coerced(self, ipc_server, fake_service):
        """``duration="7.5"`` → 7.5 (preserves the documented string coercion)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": "7.5"}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=7.5,
            filters=None,
        )

    def test_missing_duration_falls_back_to_default_10(self, ipc_server, fake_service):
        """Empty payload → duration defaults to 10.0 (historical default)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=10.0,
            filters=None,
        )

    def test_invalid_duration_type_returns_invalid_field(self, ipc_server, fake_service):
        """``duration=["list"]`` → ``code: invalid_field`` (not in (int, float, str))."""
        resp = ipc_server._handle_microphone_test_start({"duration": ["not", "a", "number"]}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "duration"
        fake_service.microphone_test_start.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# run_prewarm / open_prewarm_log fixed-string error messages
# ────────────────────────────────────────────────────────────────────────────
#
# (Wave 3, 2026-08-14): The ``_handle_run_prewarm`` and
# ``_handle_open_prewarm_log`` handlers were REMOVED entirely (prewarm
# became a worker startup phase — master plan §6.2 P-1). The four
# tests that pinned the DE-46 fixed-string-no-echo invariant on those
# handlers were deleted in lockstep. The DE-46 invariant itself is
# still pinned by the surviving ``TestNoStrEcho`` suite (other
# handlers in the slice that have specific-exception branches with
# the same fixed-string pattern).
#
# (2026-08-14, later): ``_handle_open_prewarm_log`` was RESTORED
# verbatim from 5a319872 (plan §6.3 addendum — Cache Status card);
# it now opens ``worker.log`` and keeps the DE-46 fixed-string pattern.
# ``_handle_run_prewarm`` was also restored (addendum 2nd half),
# re-implemented to re-run the warm phase in-process (see
# ``prewarm.status.run_prewarm_now``) — the DE-46 fixed-string
# invariant is pinned by ``TestRunPrewarm`` in
# ``tests/handlers/test_status_handlers.py``.


# ────────────────────────────────────────────────────────────────────────────
# Cross-cutting: existing test contracts preserved
# ────────────────────────────────────────────────────────────────────────────


class TestExistingContractsPreserved:
    """Regression guards: existing handler contracts still hold after the DE-2H fixes."""

    def test_microphone_test_string_duration_still_coerced(self, ipc_server, fake_service):
        """Existing contract: ``"7.5" → 7.5`` (documented string coercion)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": "7.5"}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=7.5,
            filters=None,
        )

    def test_get_status_dict_return_value_still_passes_through(self, ipc_server, fake_service):
        """Existing contract: dict return value passes through unchanged."""
        fake_service.get_status.return_value = {
            "status": "recording",
            "xruns_since_start": 2,
        }
        resp = ipc_server._handle_get_status({}, {})
        assert resp["type"] == "status"
        assert resp["data"] == {"status": "recording", "xruns_since_start": 2}

    def test_get_status_legacy_string_return_value_wrapped(self, ipc_server, fake_service):
        """Existing contract: legacy string return value wrapped in dict."""
        fake_service.get_status.return_value = "recording"
        resp = ipc_server._handle_get_status({}, {})
        assert resp["type"] == "status"
        assert resp["data"] == {"status": "recording"}
