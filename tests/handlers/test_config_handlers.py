"""Unit tests for ``ConfigHandlersMixin`` (CR-12).

Covers the 3 config IPC handlers defined in
``voice_typer/server/handlers/config_handlers.py``:

- ``_handle_get_config`` — returns ``{type: config, data: <sanitized>}``.
- ``_handle_get_defaults`` — returns ``{type: defaults, data: <defaults>}``.
- ``_handle_set_config`` — validates, applies, returns ``{type: ack}``
  with optional ``data: {accepted, rejected}`` for unknown keys.

The set_config handler is the most complex: it rejects non-dict
payloads, runs the payload through ``validate_config_update`` (which
drops unknown keys and validates field types/ranges atomically),
and triggers a model-backend swap if ``model_size`` / ``asr_backend``
changed.
"""

from __future__ import annotations

import logging


class TestGetConfig:
    """``_handle_get_config`` — returns sanitized config view."""

    def test_happy_path_returns_config_type(self, ipc_server, fake_service):
        """Valid call → ``{type: config, data: <service.get_config() output>}``.

        SEC-003: the service layer is responsible for redacting secret
        fields (api keys etc.) — the handler just passes the sanitized
        dict through.  We assert the handler doesn't add or strip any
        keys.
        """
        sanitized = {"hotkey": "<f2>", "model_size": "tiny", "openai_api_key": "<redacted>"}
        fake_service.get_config.return_value = sanitized

        resp = ipc_server._handle_get_config({}, {})

        assert resp["type"] == "config"
        assert resp["data"] is sanitized, "handler must pass the dict through verbatim"
        fake_service.get_config.assert_called_once_with()

    def test_service_returns_empty_dict(self, ipc_server, fake_service):
        """Empty config → ``{type: config, data: {}}`` (no crash)."""
        fake_service.get_config.return_value = {}
        resp = ipc_server._handle_get_config({}, {})
        assert resp["type"] == "config"
        assert resp["data"] == {}


class TestGetDefaults:
    """``_handle_get_defaults`` — returns the default Config() values."""

    def test_happy_path_returns_defaults_type(self, ipc_server, fake_service):
        fake_service.get_defaults.return_value = {"hotkey": "<f2>", "model_size": "tiny"}
        resp = ipc_server._handle_get_defaults({}, {})
        assert resp["type"] == "defaults"
        assert resp["data"] == {"hotkey": "<f2>", "model_size": "tiny"}
        fake_service.get_defaults.assert_called_once_with()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        """G4-CR-09: a service exception in ``get_defaults`` must produce
        the generic WS-path envelope
        ``{code: 'server.internal_error', message: 'internal error'}``."""
        fake_service.get_defaults.side_effect = RuntimeError("defaults unavailable")
        resp = ipc_server._handle_get_defaults({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestSetConfig:
    """``_handle_set_config`` — validate, apply, return ack."""

    def test_non_dict_payload_returns_error(self, ipc_server):
        """NEW-IPC-005: non-dict ``data`` → explicit error (not silent no-op).

        Pre-fix, a list/string/None payload silently skipped the
        setattr block but still returned ``{type: ack}`` — the worst
        IPC failure mode (silent success on bad input).
        """
        resp = ipc_server._handle_set_config(["not", "a", "dict"], {})
        assert resp["type"] == "error"
        assert "data: object" in resp["data"]["message"]

    def test_happy_path_valid_payload_returns_plain_ack(self, ipc_server, fake_service):
        """Valid payload with only allowlisted keys → ``{type: ack}`` (no data).

        NEW-IPC-015: the common case (all keys accepted) returns a
        plain ``{type: ack}`` matching existing callers.  Only when
        there are rejected keys is the ``data: {accepted, rejected}``
        field added.
        """
        # hotkey is in IPC_CONFIG_ALLOWLIST.
        resp = ipc_server._handle_set_config({"hotkey": "<f3>"}, {})
        assert resp["type"] == "ack"
        # No rejected keys → no "data" key (the dispatcher's
        # ``setdefault("data", {})`` adds one later, but the handler
        # itself returns just ``{type: ack}``).
        assert "data" not in resp or resp["data"] == {}, "happy-path ack should not carry accepted/rejected lists"
        # Service.apply_config must be called with the validated dict.
        fake_service.apply_config.assert_called_once()
        applied = fake_service.apply_config.call_args[0][0]
        assert applied == {"hotkey": "<f3>"}, "apply_config must receive validated dict"

    def test_unknown_keys_silently_dropped_and_echoed_in_rejected(self, ipc_server):
        """Unknown keys are dropped, but the ack carries ``rejected`` list.

        NEW-IPC-015: the renderer uses ``data.rejected`` to surface
        "these fields were silently dropped" to the user (e.g. typo'd
        field name).
        """
        resp = ipc_server._handle_set_config({"hotkey": "<f3>", "totally_unknown_field": "x"}, {})
        assert resp["type"] == "ack"
        assert resp["data"]["rejected"] == ["totally_unknown_field"]
        assert resp["data"]["accepted"] == ["hotkey"]

    def test_validation_error_returns_error_with_first_message(self, ipc_server):
        """``validate_config_update`` returns errors → error response.

        The dispatcher treats the payload atomically: the first
        validation error aborts the entire payload (no partial apply).
        """
        # model_size has an enum validator that rejects unknown values.
        resp = ipc_server._handle_set_config({"model_size": "not-a-real-model"}, {})
        assert resp["type"] == "error"
        assert "message" in resp["data"]
        # The error message is the first (and only) validation error.
        assert isinstance(resp["data"]["message"], str)
        assert len(resp["data"]["message"]) > 0

    def test_model_size_change_triggers_service_change_model(self, ipc_server, fake_app, fake_service):
        """NEW-IPC-016: when ``model_size`` differs from current, call
        ``service.change_model()`` so the next dictation uses the new
        model without a restart.
        """
        # Configure the fake app's current model_size so the
        # "differs from current" branch is taken.
        fake_app.config.model_size = "tiny"
        resp = ipc_server._handle_set_config({"model_size": "large-v3-turbo"}, {})
        assert resp["type"] == "ack"
        fake_service.change_model.assert_called_once_with("large-v3-turbo")

    def test_change_model_failure_does_not_abort_set_config(self, ipc_server, fake_app, fake_service):
        """If ``service.change_model()`` raises, the handler logs and
        continues — set_config must still apply the rest of the payload
        and return ack.

        A failure to swap the active engine shouldn't lose the user's
        config save (they'd be confused: "I changed the model, clicked
        save, and nothing happened AND my other settings are gone").
        """
        fake_app.config.model_size = "tiny"
        fake_service.change_model.side_effect = RuntimeError("engine busy")
        resp = ipc_server._handle_set_config({"model_size": "large-v3-turbo"}, {})
        # Still ack — the model swap failure is non-fatal.
        assert resp["type"] == "ack"
        # The rest of the payload was still applied.
        fake_service.apply_config.assert_called_once()


# ==============================================================================
# Merged from tests/test_config_onboarding_handler_fixes.py —
#   config-handler regression pins (failed model/backend values not persisted or echoed, missing
#   config-mutation-lock warning)
# ==============================================================================


class TestFailedModelConfigNotPersisted:
    """DE-6: ``change_model`` / ``set_active_backend`` failures must be
    dropped from the ``apply_config`` payload AND from the ``applied``
    echo list AND from the ``config_changed`` event payload."""

    def test_change_model_failure_drops_model_size_from_apply_config(self, ipc_server, fake_app, fake_service):
        """When ``change_model`` raises, ``apply_config`` must NOT
        receive ``model_size`` — otherwise the failed value is written
        to config.json, leaving on-disk state pointing at a model the
        running engine refused to load."""
        fake_app.config.model_size = "tiny"
        fake_service.change_model.side_effect = RuntimeError("engine busy")

        ipc_server._handle_set_config({"model_size": "large-v3-turbo"}, {})

        fake_service.apply_config.assert_called_once()
        applied_arg = fake_service.apply_config.call_args[0][0]
        assert "model_size" not in applied_arg, (
            f"DE-6: apply_config must NOT receive the failed model_size; got: {applied_arg!r}"
        )
        assert applied_arg == {}, (
            "DE-6: with only the failed key in the payload, apply_config should receive an empty dict"
        )

    def test_change_model_failure_drops_model_size_from_applied_list(self, ipc_server, fake_app, fake_service):
        """The ``applied`` list echoed in the partial-success envelope
        must NOT contain a key whose swap failed — otherwise the
        envelope contradicts itself (``model_errors`` says it failed,
        ``applied`` says it succeeded)."""
        fake_app.config.model_size = "tiny"
        fake_service.change_model.side_effect = RuntimeError("engine busy")

        resp = ipc_server._handle_set_config({"model_size": "large-v3-turbo"}, {})

        assert resp["type"] == "ack"
        assert resp["data"]["status"] == "partial"
        assert "model_size" not in resp["data"].get("applied", []), (
            f"DE-6: failed key must not appear in `applied` list; got: {resp['data'].get('applied')!r}"
        )
        # model_errors still reports the failure so the renderer can
        # surface the partial-success toast.
        assert resp["data"]["model_errors"], "model_errors envelope must still report the failure"

    def test_change_model_failure_preserves_other_keys_in_apply_config(self, ipc_server, fake_app, fake_service):
        """When ``change_model`` fails but the payload also contains
        unrelated allowlisted keys, only ``model_size`` is dropped —
        the rest must still reach ``apply_config``."""
        fake_app.config.model_size = "tiny"
        fake_service.change_model.side_effect = RuntimeError("engine busy")

        # ``hotkey`` is an allowlisted key that does NOT trigger a
        # model/backend swap, so it must survive the failed_keys filter.
        ipc_server._handle_set_config({"model_size": "large-v3-turbo", "hotkey": "<f3>"}, {})

        fake_service.apply_config.assert_called_once()
        applied_arg = fake_service.apply_config.call_args[0][0]
        assert "model_size" not in applied_arg
        assert applied_arg.get("hotkey") == "<f3>", (
            f"DE-6: unrelated key must survive the failed_keys filter; got: {applied_arg!r}"
        )

    def test_set_active_backend_failure_drops_asr_backend_from_apply_config(self, ipc_server, fake_app, fake_service):
        """Symmetric to ``change_model``: when ``set_active_backend``
        raises, ``asr_backend`` must be dropped from the
        ``apply_config`` payload."""
        fake_app.config.asr_backend = "whisper"
        fake_service.set_active_backend.side_effect = RuntimeError("backend unavailable")

        ipc_server._handle_set_config({"asr_backend": "qwen"}, {})

        fake_service.apply_config.assert_called_once()
        applied_arg = fake_service.apply_config.call_args[0][0]
        assert "asr_backend" not in applied_arg, (
            f"DE-6: apply_config must NOT receive failed asr_backend; got: {applied_arg!r}"
        )

    def test_config_changed_event_excludes_failed_keys(self, ipc_server, fake_app, fake_service, monkeypatch):
        """DE-6: the ``config_changed`` event published to the
        renderer must NOT carry the failed model value — otherwise the
        renderer mirrors the stale value into its local config state
        (UI shows "model: medium" while the running engine is still
        on "small")."""
        fake_app.config.model_size = "tiny"
        fake_service.change_model.side_effect = RuntimeError("engine busy")

        published_events: list[dict] = []
        import voice_typer.server.handlers.config_handlers as ch_mod

        def fake_publish(event):
            published_events.append(event)

        monkeypatch.setattr(ch_mod.event_bus, "publish", fake_publish)

        ipc_server._handle_set_config({"model_size": "large-v3-turbo", "hotkey": "<f3>"}, {})

        config_changed_events = [e for e in published_events if e.get("type") == "config_changed"]
        assert config_changed_events, "config_changed event must be published"
        event_data = config_changed_events[0]["data"]
        assert "model_size" not in event_data, (
            f"DE-6: config_changed event must not carry failed model_size; got: {event_data!r}"
        )
        assert event_data.get("hotkey") == "<f3>", (
            f"DE-6: config_changed event must still carry non-failed keys; got: {event_data!r}"
        )


class TestMissingConfigLockWarning:
    """DE-37: when ``self.app._config_mutation_lock`` is missing, the
    handler logs a WARNING once per process (instead of silently
    running lock-free)."""

    def test_missing_lock_emits_warning(self, ipc_server, fake_app, caplog):
        """First call with no lock → WARNING in the log."""
        # Ensure the fake app has no ``_config_mutation_lock`` attribute
        # (MagicMock auto-vivifies — explicitly delete it).
        if hasattr(fake_app, "_config_mutation_lock"):
            del fake_app._config_mutation_lock
        # Reset the module-level "warned once" flag so this test is
        # order-independent.
        import voice_typer.server.handlers.config_handlers as ch_mod

        ch_mod._CONFIG_LOCK_MISSING_WARNED = False

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.ipc_server"):
            ipc_server._handle_set_config({"hotkey": "<f3>"}, {})

        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "_config_mutation_lock" in r.getMessage()
        ]
        assert warnings, "DE-37: missing _config_mutation_lock must emit a WARNING"

    def test_missing_lock_warning_fires_only_once_per_process(self, ipc_server, fake_app, caplog):
        """Second call with no lock → no second WARNING (once per process)."""
        if hasattr(fake_app, "_config_mutation_lock"):
            del fake_app._config_mutation_lock
        import voice_typer.server.handlers.config_handlers as ch_mod

        ch_mod._CONFIG_LOCK_MISSING_WARNED = False

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.ipc_server"):
            ipc_server._handle_set_config({"hotkey": "<f3>"}, {})
            caplog.clear()
            ipc_server._handle_set_config({"hotkey": "<f4>"}, {})

        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "_config_mutation_lock" in r.getMessage()
        ]
        assert not warnings, f"DE-37: warning must fire only ONCE per process; got a second warning: {warnings!r}"

    def test_present_lock_emits_no_warning(self, ipc_server, fake_app, caplog):
        """When the lock is present (real AppProtocol), NO warning fires."""
        import threading

        # Provide a real RLock so the handler acquires it.
        fake_app._config_mutation_lock = threading.RLock()
        import voice_typer.server.handlers.config_handlers as ch_mod

        ch_mod._CONFIG_LOCK_MISSING_WARNED = False

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.ipc_server"):
            ipc_server._handle_set_config({"hotkey": "<f3>"}, {})

        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "_config_mutation_lock" in r.getMessage()
        ]
        assert not warnings, "DE-37: when the lock is present, no warning should fire"
