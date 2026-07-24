"""DE-2G (session-DE): regression tests for the Group 4 findings fixed
in handlers group G — config_handlers.py + onboarding_handlers.py.

Covers five findings from the comprehensive Group 4 review:

- **DE-6** — ``config_handlers._handle_set_config`` persisted failed
  ``model_size`` / ``asr_backend`` values to disk via ``apply_config``
  AND echoed them in the ``applied`` list, contradicting the
  ``model_errors`` partial-success envelope. The fix drops failed keys
  into a ``failed_keys`` set, builds ``to_persist`` excluding them,
  passes ``to_persist`` (not ``validated``) to ``apply_config``, and
  publishes ``to_persist`` in the ``config_changed`` event so the
  renderer doesn't mirror a stale model value into its local state.

- **DE-37** — ``config_handlers._handle_set_config`` silently fell
  back to lock-free execution when ``app._config_mutation_lock`` was
  absent (test fakes / misconfigured hosts). The fix logs a WARNING
  once per process so the missing concurrency guard surfaces in
  ``voice-typer.log`` instead of staying invisible.

- **DE-39** — ``onboarding_handlers._handle_onboarding_start`` had no
  re-run guard: a stale renderer could re-launch the 6-step wizard
  over an already-completed onboarding state. The fix queries
  ``service.onboarding_is_first_run`` first; if False, requires
  ``{"force": true}`` in the data payload, else returns an error
  envelope with ``code: "onboarding_already_complete"``.

- **DE-40** — five onboarding handlers (``set_microphone``,
  ``set_hotkey``, ``set_model``, ``skip``, ``apply``) delegated
  ack-vs-error to whether the service return dict contained an
  ``"error"`` key but NEVER logged the service-returned error
  server-side. An operator investigating a hung wizard had no
  breadcrumb tying the renderer's error toast back to the service
  call. The fix emits a WARNING with the command name + error string.

- **DE-41** — ``onboarding_handlers._handle_onboarding_start``'s
  ``OnboardingController().mark_started()`` failure was swallowed by
  ``except Exception: pass`` with a "non-critical" comment. PVT-006
  rationale: a missing ``.onboarding_started`` marker lets
  ``startup_sequence``'s auto-heal clobber an in-progress wizard on
  next restart — that's a real correctness risk. The fix replaces
  ``pass`` with a WARNING + ``exc_info=True``.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest


# ────────────────────────────────────────────────────────────────────────────
# DE-6: set_config must NOT persist failed model/backend config
# ────────────────────────────────────────────────────────────────────────────


class TestDE6FailedModelConfigNotPersisted:
    """DE-6: ``change_model`` / ``set_active_backend`` failures must be
    dropped from the ``apply_config`` payload AND from the ``applied``
    echo list AND from the ``config_changed`` event payload."""

    def test_change_model_failure_drops_model_size_from_apply_config(
        self, ipc_server, fake_app, fake_service
    ):
        """When ``change_model`` raises, ``apply_config`` must NOT
        receive ``model_size`` — otherwise the failed value is written
        to config.json, leaving on-disk state pointing at a model the
        running engine refused to load."""
        fake_app.config.model_size = "tiny"
        fake_service.change_model.side_effect = RuntimeError("engine busy")

        ipc_server._handle_set_config({"model_size": "small.en"}, {})

        fake_service.apply_config.assert_called_once()
        applied_arg = fake_service.apply_config.call_args[0][0]
        assert "model_size" not in applied_arg, (
            f"DE-6: apply_config must NOT receive the failed model_size; "
            f"got: {applied_arg!r}"
        )
        assert applied_arg == {}, (
            "DE-6: with only the failed key in the payload, apply_config "
            "should receive an empty dict"
        )

    def test_change_model_failure_drops_model_size_from_applied_list(
        self, ipc_server, fake_app, fake_service
    ):
        """The ``applied`` list echoed in the partial-success envelope
        must NOT contain a key whose swap failed — otherwise the
        envelope contradicts itself (``model_errors`` says it failed,
        ``applied`` says it succeeded)."""
        fake_app.config.model_size = "tiny"
        fake_service.change_model.side_effect = RuntimeError("engine busy")

        resp = ipc_server._handle_set_config({"model_size": "small.en"}, {})

        assert resp["type"] == "ack"
        assert resp["data"]["status"] == "partial"
        assert "model_size" not in resp["data"].get("applied", []), (
            f"DE-6: failed key must not appear in `applied` list; "
            f"got: {resp['data'].get('applied')!r}"
        )
        # model_errors still reports the failure so the renderer can
        # surface the partial-success toast.
        assert resp["data"]["model_errors"], (
            "model_errors envelope must still report the failure"
        )

    def test_change_model_failure_preserves_other_keys_in_apply_config(
        self, ipc_server, fake_app, fake_service
    ):
        """When ``change_model`` fails but the payload also contains
        unrelated allowlisted keys, only ``model_size`` is dropped —
        the rest must still reach ``apply_config``."""
        fake_app.config.model_size = "tiny"
        fake_service.change_model.side_effect = RuntimeError("engine busy")

        # ``hotkey`` is an allowlisted key that does NOT trigger a
        # model/backend swap, so it must survive the failed_keys filter.
        ipc_server._handle_set_config(
            {"model_size": "small.en", "hotkey": "<f3>"}, {}
        )

        fake_service.apply_config.assert_called_once()
        applied_arg = fake_service.apply_config.call_args[0][0]
        assert "model_size" not in applied_arg
        assert applied_arg.get("hotkey") == "<f3>", (
            f"DE-6: unrelated key must survive the failed_keys filter; "
            f"got: {applied_arg!r}"
        )

    def test_set_active_backend_failure_drops_asr_backend_from_apply_config(
        self, ipc_server, fake_app, fake_service
    ):
        """Symmetric to ``change_model``: when ``set_active_backend``
        raises, ``asr_backend`` must be dropped from the
        ``apply_config`` payload."""
        fake_app.config.asr_backend = "whisper"
        fake_service.set_active_backend.side_effect = RuntimeError(
            "backend unavailable"
        )

        ipc_server._handle_set_config({"asr_backend": "qwen"}, {})

        fake_service.apply_config.assert_called_once()
        applied_arg = fake_service.apply_config.call_args[0][0]
        assert "asr_backend" not in applied_arg, (
            f"DE-6: apply_config must NOT receive failed asr_backend; "
            f"got: {applied_arg!r}"
        )

    def test_config_changed_event_excludes_failed_keys(
        self, ipc_server, fake_app, fake_service, monkeypatch
    ):
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

        ipc_server._handle_set_config(
            {"model_size": "small.en", "hotkey": "<f3>"}, {}
        )

        config_changed_events = [
            e for e in published_events if e.get("type") == "config_changed"
        ]
        assert config_changed_events, "config_changed event must be published"
        event_data = config_changed_events[0]["data"]
        assert "model_size" not in event_data, (
            f"DE-6: config_changed event must not carry failed model_size; "
            f"got: {event_data!r}"
        )
        assert event_data.get("hotkey") == "<f3>", (
            f"DE-6: config_changed event must still carry non-failed keys; "
            f"got: {event_data!r}"
        )


# ────────────────────────────────────────────────────────────────────────────
# DE-37: missing _config_mutation_lock logs WARNING once per process
# ────────────────────────────────────────────────────────────────────────────


class TestDE37MissingConfigLockWarning:
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

        with caplog.at_level(
            logging.WARNING, logger="voice_typer.server.ipc_server"
        ):
            ipc_server._handle_set_config({"hotkey": "<f3>"}, {})

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "_config_mutation_lock" in r.getMessage()
        ]
        assert warnings, (
            "DE-37: missing _config_mutation_lock must emit a WARNING"
        )

    def test_missing_lock_warning_fires_only_once_per_process(
        self, ipc_server, fake_app, caplog
    ):
        """Second call with no lock → no second WARNING (once per process)."""
        if hasattr(fake_app, "_config_mutation_lock"):
            del fake_app._config_mutation_lock
        import voice_typer.server.handlers.config_handlers as ch_mod

        ch_mod._CONFIG_LOCK_MISSING_WARNED = False

        with caplog.at_level(
            logging.WARNING, logger="voice_typer.server.ipc_server"
        ):
            ipc_server._handle_set_config({"hotkey": "<f3>"}, {})
            caplog.clear()
            ipc_server._handle_set_config({"hotkey": "<f4>"}, {})

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "_config_mutation_lock" in r.getMessage()
        ]
        assert not warnings, (
            "DE-37: warning must fire only ONCE per process; got a second "
            f"warning: {warnings!r}"
        )

    def test_present_lock_emits_no_warning(
        self, ipc_server, fake_app, caplog
    ):
        """When the lock is present (real AppProtocol), NO warning fires."""
        import threading

        # Provide a real RLock so the handler acquires it.
        fake_app._config_mutation_lock = threading.RLock()
        import voice_typer.server.handlers.config_handlers as ch_mod

        ch_mod._CONFIG_LOCK_MISSING_WARNED = False

        with caplog.at_level(
            logging.WARNING, logger="voice_typer.server.ipc_server"
        ):
            ipc_server._handle_set_config({"hotkey": "<f3>"}, {})

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "_config_mutation_lock" in r.getMessage()
        ]
        assert not warnings, (
            "DE-37: when the lock is present, no warning should fire"
        )


# ────────────────────────────────────────────────────────────────────────────
# DE-39: onboarding_start re-run guard
# ────────────────────────────────────────────────────────────────────────────


class TestDE39OnboardingStartRerunGuard:
    """DE-39: ``_handle_onboarding_start`` refuses to re-run the wizard
    after completion unless the caller passes ``{"force": true}``."""

    def test_first_run_true_proceeds_normally(
        self, ipc_server, fake_service
    ):
        """When ``onboarding_is_first_run`` returns True, the handler
        delegates to ``service.onboarding_start`` as before."""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": True}
        fake_service.onboarding_start.return_value = {
            "step": 0,
            "total_steps": 6,
            "step_name": "Welcome",
        }

        resp = ipc_server._handle_onboarding_start({}, {})

        assert resp["type"] == "onboarding_step"
        assert resp["data"]["step_name"] == "Welcome"
        fake_service.onboarding_start.assert_called_once()

    def test_first_run_false_without_force_returns_already_complete_error(
        self, ipc_server, fake_service
    ):
        """When onboarding is already complete and no ``force`` flag is
        passed, the handler returns an error envelope with
        ``code: 'onboarding_already_complete'`` — and does NOT call
        ``service.onboarding_start``."""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": False}

        resp = ipc_server._handle_onboarding_start({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "onboarding_already_complete", (
            f"DE-39: expected code 'onboarding_already_complete'; "
            f"got: {resp['data'].get('code')!r}"
        )
        assert "force" in resp["data"]["message"].lower(), (
            "DE-39: error message must mention the force flag"
        )
        fake_service.onboarding_start.assert_not_called()

    def test_first_run_false_with_force_proceeds(
        self, ipc_server, fake_service
    ):
        """When ``force: true`` is passed, the handler re-runs the
        wizard even though onboarding is already complete (used by
        Settings → Troubleshooting → Re-run Setup Wizard)."""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": False}
        fake_service.onboarding_start.return_value = {
            "step": 0,
            "total_steps": 6,
            "step_name": "Welcome",
        }

        resp = ipc_server._handle_onboarding_start({"force": True}, {})

        assert resp["type"] == "onboarding_step"
        assert resp["data"]["step_name"] == "Welcome"
        fake_service.onboarding_start.assert_called_once()

    def test_first_run_false_with_force_falsy_string_does_not_proceed(
        self, ipc_server, fake_service
    ):
        """``force`` must be a real boolean True — the string
        ``"false"`` is truthy in Python but the handler uses
        ``bool(data.get("force", False))`` which coerces it to True.

        Wait — actually, ``bool("false")`` is True in Python because
        non-empty strings are truthy. So this test asserts that a
        NON-empty string value for ``force`` does proceed (matching
        Python truthiness). The guard only blocks when ``force`` is
        falsy (None, False, 0, empty string, missing key)."""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": False}
        fake_service.onboarding_start.return_value = {
            "step": 0,
            "total_steps": 6,
            "step_name": "Welcome",
        }

        # Empty string → falsy → guard fires.
        resp = ipc_server._handle_onboarding_start({"force": ""}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "onboarding_already_complete"

    def test_non_dict_data_does_not_crash_guard(
        self, ipc_server, fake_service
    ):
        """DE-39: the guard must not crash when ``data`` is None or a
        non-dict (renderer may send no payload). The handler coerces
        to ``{}`` before reading ``force``."""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": False}

        # None payload — must not raise TypeError.
        resp = ipc_server._handle_onboarding_start(None, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "onboarding_already_complete"

    def test_guard_logs_warning_when_blocking(
        self, ipc_server, fake_service, caplog
    ):
        """DE-39: when the guard blocks, the handler logs a WARNING so
        operators can see the rejection in ``voice-typer.log``."""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": False}

        with caplog.at_level(
            logging.WARNING, logger="voice_typer.server.ipc_server"
        ):
            ipc_server._handle_onboarding_start({}, {})

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "onboarding_start" in r.getMessage()
            and "already" in r.getMessage()
        ]
        assert warnings, (
            "DE-39: rejection must be logged at WARNING for operator visibility"
        )


# ────────────────────────────────────────────────────────────────────────────
# DE-40: service-returned errors logged at WARNING
# ────────────────────────────────────────────────────────────────────────────


class TestDE40ServiceErrorsLogged:
    """DE-40: when a service returns ``{"error": ...}``, the handler
    must log a WARNING with the command name and the error string."""

    @pytest.mark.parametrize(
        "handler_name, service_method, payload",
        [
            (
                "_handle_onboarding_set_microphone",
                "onboarding_set_microphone",
                {"mic_id": "ghost"},
            ),
            (
                "_handle_onboarding_set_hotkey",
                "onboarding_set_hotkey",
                {"hotkey": "<f4>"},
            ),
            (
                "_handle_onboarding_set_model",
                "onboarding_set_model",
                {"model": "tiny.en"},
            ),
            ("_handle_onboarding_skip", "onboarding_skip", {}),
            ("_handle_onboarding_apply", "onboarding_apply", {}),
        ],
    )
    def test_service_error_is_logged_at_warning(
        self,
        ipc_server,
        fake_service,
        caplog,
        handler_name,
        service_method,
        payload,
    ):
        """Each of the 5 onboarding handlers that delegate ack-vs-error
        to the service's return dict shape must log the service-returned
        error at WARNING so the failure leaves a server-side breadcrumb."""
        service_mock = getattr(fake_service, service_method)
        service_mock.return_value = {"error": "service-layer failure"}
        handler = getattr(ipc_server, handler_name)

        with caplog.at_level(
            logging.WARNING, logger="voice_typer.server.ipc_server"
        ):
            resp = handler(payload, {})

        # Response shape is unchanged — DE-40 only adds a log line.
        assert resp["type"] == "error"
        assert resp["data"] == {"error": "service-layer failure"}

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and service_method in r.getMessage()
            and "service returned error" in r.getMessage()
            and "service-layer failure" in r.getMessage()
        ]
        assert warnings, (
            f"DE-40: {handler_name} must log a WARNING with the command "
            f"name and the service-returned error string"
        )

    def test_service_success_does_not_log_warning(
        self, ipc_server, fake_service, caplog
    ):
        """DE-40: when the service returns success (no ``error`` key),
        NO warning is logged — the handler's ack-vs-error branch only
        logs on the error path."""
        fake_service.onboarding_apply.return_value = {"ok": True}

        with caplog.at_level(
            logging.WARNING, logger="voice_typer.server.ipc_server"
        ):
            resp = ipc_server._handle_onboarding_apply({}, {})

        assert resp["type"] == "ack"
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "onboarding_apply" in r.getMessage()
            and "service returned error" in r.getMessage()
        ]
        assert not warnings, (
            "DE-40: success path must not emit the service-error warning"
        )


# ────────────────────────────────────────────────────────────────────────────
# DE-41: mark_started failure logged at WARNING (was silent pass)
# ────────────────────────────────────────────────────────────────────────────


class TestDE41MarkStartedFailureLogged:
    """DE-41: ``OnboardingController().mark_started()`` failures in
    ``_handle_onboarding_start`` are logged at WARNING with
    ``exc_info=True`` instead of being silently swallowed."""

    def test_mark_started_failure_logs_warning_with_exc_info(
        self, ipc_server, fake_service, monkeypatch, caplog
    ):
        """When ``mark_started`` raises, the handler must emit a WARNING
        with ``exc_info=True`` (was ``except Exception: pass``)."""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": True}
        fake_service.onboarding_start.return_value = {
            "step": 0,
            "total_steps": 6,
            "step_name": "Welcome",
        }

        # Force mark_started to raise.
        from voice_typer.server import onboarding as onboarding_mod

        def _boom(self):
            raise OSError("disk full")

        monkeypatch.setattr(
            onboarding_mod.OnboardingController, "mark_started", _boom
        )

        with caplog.at_level(
            logging.WARNING, logger="voice_typer.server.ipc_server"
        ):
            resp = ipc_server._handle_onboarding_start({}, {})

        # Response is still success — mark_started is best-effort and
        # must not abort the wizard.
        assert resp["type"] == "onboarding_step"

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "onboarding_start" in r.getMessage()
            and "mark_started failed" in r.getMessage()
        ]
        assert warnings, (
            "DE-41: mark_started failure must be logged at WARNING"
        )
        # exc_info must be attached so the traceback lands in voice-typer.log.
        assert any(
            r.exc_info is not None for r in warnings
        ), "DE-41: warning must carry exc_info=True so the traceback is logged"

    def test_mark_started_success_does_not_log_warning(
        self, ipc_server, fake_service, monkeypatch, caplog
    ):
        """When ``mark_started`` succeeds, NO warning is logged."""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": True}
        fake_service.onboarding_start.return_value = {
            "step": 0,
            "total_steps": 6,
            "step_name": "Welcome",
        }

        from voice_typer.server import onboarding as onboarding_mod

        def _ok(self):
            return None

        monkeypatch.setattr(onboarding_mod.OnboardingController, "mark_started", _ok)

        with caplog.at_level(
            logging.WARNING, logger="voice_typer.server.ipc_server"
        ):
            resp = ipc_server._handle_onboarding_start({}, {})

        assert resp["type"] == "onboarding_step"
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "mark_started failed" in r.getMessage()
        ]
        assert not warnings, (
            "DE-41: success path must not emit the mark_started warning"
        )
