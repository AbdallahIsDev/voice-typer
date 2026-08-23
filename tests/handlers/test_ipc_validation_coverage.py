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

# ── 1. download_model ────────────────────────────────────────────────────


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
